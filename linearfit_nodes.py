from pyiron_workflow import Workflow
import pandas as pd
import numpy as np
from dataclasses import dataclass, field, asdict
from typing import Optional
import matplotlib.pyplot as plt


@dataclass
class EmbeddingsALL:
    npot: str = "FinnisSinclairShiftedScaled"
    fs_parameters: list[int] = field(default_factory=lambda: [1, 1])
    ndensity: int = 1

@dataclass
class Embeddings:
    ALL: EmbeddingsALL = field(default_factory=EmbeddingsALL)

@dataclass
class BondsALL:
    radbase: str = 'SBessel'
    radparameters: list[float] = field(default_factory=lambda: [5.25])
    rcut: float | int = 7.0
    dcut: float = 0.01

@dataclass
class Bonds:
    ALL: BondsALL = field(default_factory=BondsALL)

@dataclass
class FunctionsALL:
    nradmax_by_orders: list[int] = field(default_factory=lambda: [15,3,2,1])
    lmax_by_orders: list[int] = field(default_factory=lambda: [0,3,2,1])


@dataclass
class Functions:
    number_of_functions_per_element: Optional[int] = None
    ALL: FunctionsALL = field(default_factory=FunctionsALL)

@dataclass
class PotentialConfig:
    deltaSplineBins: float = 0.001
    elements: list[str] | None = None

    embeddings: Embeddings = field(default_factory=Embeddings)
    bonds: Bonds = field(default_factory=Bonds)
    functions: Functions = field(default_factory=Functions)

    def __post_init__(self):
        if not isinstance(self.embeddings, Embeddings):
            self.embeddings = Embeddings()
        if not isinstance(self.bonds, Bonds):
            self.bonds = Bonds()
        if not isinstance(self.functions, Functions):
            self.functions = Functions()
    
    def to_dict(self):
        def remove_none(d):
            """Recursively remove None values from dictionaries."""
            if isinstance(d, dict):
                return {k: remove_none(v) for k, v in d.items() if v is not None}
            elif isinstance(d, list):
                return [remove_none(v) for v in d if v is not None]
            else:
                return d

        return remove_none(asdict(self))

@Workflow.wrap.as_function_node
def ReadPickledDatasetAsDataframe(file_path:str = '', compression:str = 'gzip'):

    from ase.atoms import Atoms as aseAtoms

    df = pd.read_pickle(file_path,compression=compression)

    # Atoms check
    if 'atoms' in df.columns:
        at = df.iloc[0]['atoms']
        # Checking that the elements themselves have the correct atoms format
        if isinstance(at,aseAtoms):
            df.rename(columns={"atoms": "ase_atoms"}, inplace=True)
    elif "ase_atoms" not in df.columns:
        raise ValueError(
            "DataFrame should contain 'atoms' or 'ase_atoms' (ASE atoms) columns"
        )
    
    # NUMBER OF ATOMS check
    if "NUMBER_OF_ATOMS" not in df.columns and "number_of_atoms" in df.columns:
        df.rename(columns={"number_of_atoms": "NUMBER_OF_ATOMS"}, inplace=True)
    
    df["NUMBER_OF_ATOMS"] = df["NUMBER_OF_ATOMS"].astype(int)

    # energy corrected check
    if "energy_corrected" not in df.columns and "energy" in df.columns:
        df.rename(columns={"energy": "energy_corrected"}, inplace=True)
        
    if "pbc" not in df.columns:
        df["pbc"] = df["ase_atoms"].map(lambda atoms: np.all(atoms.pbc))
    
    return df

@Workflow.wrap.as_function_node
def GetElementList(df: pd.DataFrame) -> list:
    '''
    Returns the elements list from the dataset dataframe
    '''
    # Automatically determine the list of elements
    elements_set = set()
    for at in df["ase_atoms"]:
        elements_set.update(at.get_chemical_symbols())
    elements = sorted(elements_set)
    return elements

@Workflow.wrap.as_function_node
def ParameterizePotentialConfig(
    nrad_max: list =[15, 6, 4, 1], 
    l_max:list = [0, 6, 5, 1], 
    # number_of_functions_per_element: int | None = None, 
    number_of_functions_per_element: int = 10,
    rcut: float | int = 7.0
    ):
    
    potential_config = PotentialConfig()

    potential_config.bonds.ALL.rcut = rcut
    potential_config.functions.ALL.nradmax_by_orders = nrad_max
    potential_config.functions.ALL.lmax_by_orders = l_max
    potential_config.functions.number_of_functions_per_element = number_of_functions_per_element

    return potential_config

@Workflow.wrap.as_function_node("bconf")
def CreateEmptyBasisFunctions(potential_config:PotentialConfig):

    '''
    Returns the empty basis function
    '''
    
    from pyace import create_multispecies_basis_config

    potential_config_dict = potential_config.to_dict()
    bconf = create_multispecies_basis_config(potential_config_dict)

    return bconf

@Workflow.wrap.as_function_node
def SplitTrainingAndTesting(data_df:pd.DataFrame, training_frac:float | int = 0.5, random_state = 42):
    '''
    Splits the filtered dataframe into training and testing sets based on a fraction of the dataset

    Args:
        data_df: A pandas.DataFrame of the filtered data DataFrame
        training_frac: A float number which dictates what is the precentage of the dataset to be used for training should be set between 0 to 1
        random_state (default = 42): Sets the random seed used to shuffle the data

    Returns:
        df_training: The training dataframe
        df_testing: The testing dataframe
    '''
    if isinstance(training_frac, float):
        training_frac = np.abs(training_frac)
    
    if training_frac > 1:
        print("Can't have the training dataset more than 100 \% of the dataset\n\
            Setting the value to 100%")
        training_frac = 1
    elif training_frac == 0:
        print("Can'fit with no training dataset\nSetting the value to 1%")
        training_frac = 0.01
    df_training = data_df.sample(frac=training_frac,random_state = random_state)
    df_testing = data_df.loc[(i for i in data_df.index if i not in df_training.index)]
    return df_training, df_testing

@Workflow.wrap.as_function_node
def PrepareLinearACEdataset(potential_config, df_train: pd.DataFrame, df_test: pd.DataFrame, verbose: bool = False):
    from pyace import create_multispecies_basis_config
    from pyace.linearacefit import LinearACEDataset

    elements_set = set()
    for at in df_train["ase_atoms"]:
        elements_set.update(at.get_chemical_symbols())
    for at in df_test["ase_atoms"]:
        elements_set.update(at.get_chemical_symbols())
    
    elements = sorted(elements_set)
    potential_config.elements = elements
    potential_config_dict = potential_config.to_dict()
    bconf = create_multispecies_basis_config(potential_config_dict)
    
    train_ds = LinearACEDataset(bconf,df_train)
    train_ds.construct_design_matrix(verbose=verbose)
    if df_test.empty is False:
        test_ds = LinearACEDataset(bconf,df_test)
        test_ds.construct_design_matrix(verbose=verbose)
    else:
        test_ds = None

    return train_ds, test_ds

@Workflow.wrap.as_function_node
def RunLinearFit(train_ds, test_ds = None):

    from pyace.linearacefit import LinearACEFit

    linear_fit = LinearACEFit(train_dataset= train_ds)
    linear_fit.fit()        

    training_dict = linear_fit.compute_errors(train_ds)
    training_e_rmse = round(training_dict['epa_rmse'] * 1000, 2)
    training_f_rmse = round(training_dict['f_comp_rmse'] * 1000, 2)
    print("====================== TRAINING INFO ======================")
    print(f"Training E RMSE: {training_e_rmse:.2f} meV/atom")
    print(f"Training F RMSE: {training_f_rmse:.2f} meV/A")

    if test_ds is not None:
        testing_dict = linear_fit.compute_errors(test_ds)
        testing_e_rmse = round(testing_dict['epa_rmse'] * 1000, 2)
        testing_f_rmse = round(testing_dict['f_comp_rmse'] * 1000, 2)
        print("======================= TESTING INFO =======================")
        print(f"Testing E RMSE: {testing_e_rmse:.2f} meV/atom")
        print(f"Testing F RMSE: {testing_f_rmse:.2f} meV/A")

    basis = linear_fit.get_bbasis()
    return basis

@Workflow.wrap.as_function_node
def SavePotential(basis, filename:str = ""):
    import os
    
    if filename == "":
        filename = f"{'_'.join(basis.elements_name)}_linear_potential"
        folder_name = "Linear_ace_potentials"
    else:
        folder_name = os.path.dirname(filename)
        filename = os.path.basename(filename)
    
    folder_name = "Linear_ace_potentials"
    os.makedirs(folder_name, exist_ok=True)
    
    current_path = os.getcwd()
    folder_path = current_path + '/' + folder_name
    # Saving yaml and yace files
    print(f"Potentials \"{filename}.yaml\" and \"{filename}.yace\" are saved in \"{folder_path}\".")

    yace_file_path = f"{folder_path}/{filename}.yace"
    basis.save(f"{folder_path}/{filename}.yaml")
    basis.to_ACECTildeBasisSet().save_yaml(yace_file_path)
    
    return basis, yace_file_path

@Workflow.wrap.as_function_node
def PredictEnergiesAndForces(basis,train_ds, test_ds = None):

    from pyace import PyACECalculator

    data_dict = {}

    ace = PyACECalculator(basis)

    df_training = train_ds.df
    training_structures = df_training.ase_atoms

    # Reference data
    training_number_of_atoms = df_training.NUMBER_OF_ATOMS.to_numpy()
    training_energies = df_training.energy_corrected.to_numpy()

    training_epa = training_energies / training_number_of_atoms
    training_fpa = np.concatenate(df_training.forces.to_numpy()).flatten()
    data_dict['reference_training_epa'] = training_epa
    data_dict['reference_training_fpa'] = training_fpa

    # Predicted data
    training_predict = _get_predicted_energies_forces(ace = ace, structures= training_structures)
    data_dict['predicted_training_epa'] = np.array(training_predict[0]) / training_number_of_atoms
    data_dict['predicted_training_fpa'] = np.concatenate(training_predict[1]).flatten()

    if test_ds is not None:

        df_testing = test_ds.df
        testing_structures = df_testing.ase_atoms
        
        # Reference data
        testing_number_of_atoms = df_testing.NUMBER_OF_ATOMS.to_numpy()
        testing_energies = df_testing.energy_corrected.to_numpy()

        testing_epa = testing_energies / testing_number_of_atoms
        testing_fpa = np.concatenate(df_testing.forces.to_numpy()).flatten()
        data_dict['reference_testing_epa'] = testing_epa
        data_dict['reference_testing_fpa'] = testing_fpa

        # Predicted data
        testing_predict =  _get_predicted_energies_forces(ace = ace, structures= testing_structures)
        data_dict['predicted_testing_epa'] = np.array(testing_predict[0]) / testing_number_of_atoms
        data_dict['predicted_testing_fpa'] = np.concatenate(testing_predict[1]).flatten()

    return data_dict

def _get_predicted_energies_forces(ace, structures):
    forces = []
    energies = []

    for s in structures:
        s.calc = ace
        energies.append(s.get_potential_energy())
        forces.append(s.get_forces())
        s.calc = None
    return energies, forces

def _calc_rmse(array_1,array_2,rmse_in_milli:bool = True):
    '''
    Calculates the RMSE value of two arrays

    Args:
    array_1: An array or list of energy or force values
    array_2: An array or list of energy or force values
    rmse_in_milli: (boolean, Default = True) Set False if you want the calculated RMSE value in decimals

    Returns:
    rmse: The calculated RMSE value
    '''
    rmse = np.sqrt(np.mean((array_1 - array_2) ** 2))
    if rmse_in_milli == True:
        return rmse * 1000
    else:
        return rmse
########################## PLOTTING NODES ##########################

# HISTOGRAM FOR ENERGY DISTRIBUTION
@Workflow.wrap.as_function_node
def PlotEnergyHistogram(df: pd.DataFrame, bins: int = 100, log_scale:bool = True):
    
    # Calculate energy_per_atom
    df['energy_per_atom'] = df['energy_corrected']/ df['NUMBER_OF_ATOMS']

    fig, axe = plt.subplots()
    axe.hist(df['energy_per_atom'], bins = bins, log= log_scale)
    axe.set_ylabel("Count")
    axe.set_xlabel("Energy per atom (meV/atom)")
    return fig, axe

# HISTOGRAM FOR FORCE DISTRIBUTION
@Workflow.wrap.as_function_node
def PlotForcesHistogram(df: pd.DataFrame, bins: int = 100, log_scale:bool = True):
    
    array = np.concatenate(df.forces.values).flatten()

    fig, axe = plt.subplots()

    axe.hist(array, bins = bins, log= log_scale)
    axe.set_ylabel("Count")
    axe.set_xlabel(r"Force (eV/$\mathrm{\AA}$)")
    return fig, axe

@Workflow.wrap.as_function_node
def PlotEnergyFittingCurve(data_dict: dict):

    fig, axe = plt.subplots()


    lims = [data_dict['reference_training_epa'].min(), data_dict['reference_training_epa'].max()]
    axe.plot(lims, lims, ls = '--', color = 'C0')
    
    if 'reference_testing_epa' in data_dict.keys():
        rmse_testing = _calc_rmse(data_dict['reference_testing_epa'], data_dict[f'predicted_testing_epa'])
        axe.scatter(data_dict['reference_testing_epa'], data_dict['predicted_testing_epa'],
            color = 'black', s = 30, marker = '+', label = f'Testing RMSE = {rmse_testing:.2f} (meV/atom)')
    
    rmse_training = _calc_rmse(data_dict['reference_training_epa'], data_dict['predicted_training_epa'])
    axe.scatter(data_dict['reference_training_epa'], data_dict['predicted_training_epa'],
        color = 'C0', s=30, label = f'Training RMSE = {rmse_training:.2f} (meV/atom)')

    axe.set_xlabel("DFT E (eV/atom)")
    axe.set_ylabel("Predicted E (eV/atom)")
    axe.set_title('Predicted Energy Vs Reference Energy')
    axe.legend()
    
    return fig, axe

@Workflow.wrap.as_function_node
def PlotForcesFittingCurve(data_dict: dict):

    fig, axe = plt.subplots()

    lims = [data_dict['reference_training_fpa'].min(), data_dict['reference_training_fpa'].max()]
    axe.plot(lims, lims, ls = '--', color = f'C1')
    
    if 'reference_testing_epa' in data_dict.keys():
        rmse_testing = _calc_rmse(data_dict['reference_testing_fpa'], data_dict['predicted_testing_fpa'])
        axe.scatter(data_dict['reference_testing_fpa'], data_dict['predicted_testing_fpa'],
            color = 'black', s = 30, marker = '+', label = f'Testing RMSE = {rmse_testing:.2f} (meV/$\AA$)')
    
    rmse_training = _calc_rmse(data_dict['reference_training_fpa'], data_dict['predicted_training_fpa'])
    axe.scatter(data_dict['reference_training_fpa'], data_dict['predicted_training_fpa'],
        color = 'C1', s=30, label = f'Training RMSE = {rmse_training:.2f} (meV/$\AA$)')

    axe.set_xlabel("DFT $F_i$ (eV/$\AA$)")
    axe.set_ylabel("Predicted $F_i$ (eV/$\AA$)")
    axe.set_title('Predicted Force Vs Reference Force')
    axe.legend()
    
    return fig, axe

