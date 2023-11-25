import numpy as np
import mne
from scipy.io import loadmat
import os
from Code.data_preprocess import mne_apply, bandpass_cnt
from share import datasets_basic_infos
from Code.Inner_Speech_Dataset.Python_Processing.Data_extractions import Extract_data_from_subject
from Code.Inner_Speech_Dataset.Python_Processing.Data_processing import Select_time_window, Transform_for_classificator
from mne import io, Epochs, events_from_annotations, EpochsArray
#todo:you have to observe the events of the gamified, they have to match 0,1,2,3 in the right order
def aguilera_dataset_loader(data_path: str, gamified: bool):
    # '1':'FP1', '2':'FP2', '3':'F3', '4':'F4', '5':'C3', '6':'C4', '7':'P3', '8':'P4', '9':'O1', '10':'O2', '11':'F7', '12':'F8', '13':'T7', '14':'T8', '15':'P7', '16':'P8', '17':'Fz', '18':'Cz', '19':'Pz', '20':'M1', '21':'M2', '22':'AFz', '23':'CPz', '24':POz
    # include=['Channel 3', 'Channel 4', 'Channel 5', 'Channel 6', 'Channel 7', 'Channel 8', 'Channel 11', 'Channel 12', 'Channel 13', 'Channel 14', 'Channel 15', 'Channel 16', 'Channel 17', 'Channel 18', 'Channel 19', 'Channel 23'] #this is the left and important middle
    raw = io.read_raw_edf(data_path, preload=True, verbose=40, exclude=['Gyro 1', 'Gyro 2', 'Gyro 3'])
    #raw = raw.copy().set_eeg_reference(ref_channels="average") # If I do this it, the XDAWN doesn't run.

    events, event_id = events_from_annotations(raw)
    if gamified:
        pass # There are lots of events. Find what they are.
    else:
        event_id.pop('OVTK_StimulationId_Label_05') # This is not a command
    events = events[3:] # From the one that is not a command

    # Read epochs
    epochs = Epochs(raw, events, event_id, preload=True, tmin=0, tmax=1.4, baseline=None) # Better results when there is no baseline
    label = epochs.events[:, -1]
    label = label -1 # So it goes from 0 to 3
    return epochs, label

def nieto_dataset_loader(root_dir: str, N_S: int):
    ### Hyperparameters
    # N_S: Subject number

    # Data Type
    datatype = "EEG"

    # Sampling rate
    fs = 256

    # Select the useful par of each trial. Time in seconds
    t_start = 1.5
    t_end = 3.5

    # Load all trials for a single subject
    X, Y = Extract_data_from_subject(root_dir, N_S, datatype) # This uses the derivatives folder

    # Cut useful time. i.e action interval
    X = Select_time_window(X=X, t_start=t_start, t_end=t_end, fs=fs)

    # Conditions to compared
    Conditions = [["Inner"], ["Inner"],["Inner"], ["Inner"]]
    # The class for the above condition
    Classes = [["Up"], ["Down"], ["Right"], ["Left"]]

    # Transform data and keep only the trials of interest
    X, Y = Transform_for_classificator(X, Y, Classes, Conditions)
    Y = Y.astype(int)
    event_dict = {'Arriba': 0, 'Abajo': 1, 'Derecha': 2,'Izquierda': 3}
    return X, Y, event_dict

def torres_dataset_loader(filepath: str, subject_id: int): # TODO: Flag or something to return all subjects
    EEG_nested_dict = loadmat(filepath, simplify_cells=True)
    EEG_array = np.zeros((7, 5, 33, 421, 17)) # Subject, Word, Trial, Samples, Channels

    for i_subject, subject in enumerate(EEG_nested_dict['S']):
        for i_word, word in enumerate(subject['Palabra']):
            for i_epoch, epoch in enumerate(word['Epoca']):
                if i_epoch > 32: # One subject has an extra epoch, the option was leaving one empty epoch to everyone or removing it.
                    pass # I chose removing, that's why we don't capture it.
                else:
                    EEG_array[i_subject, i_word, i_epoch, :epoch['SenalesEEG'].shape[0], :] = epoch['SenalesEEG']

    # SELECT DATA
    # word: 0 to 4, because the 5 is "Seleccionar" (Select), and we are not going to use it.
    # channel 0:13 because 14, 15, 16 are gyros and marker of start and end.
    # EEG_array_selected_values is (word, trials, samples, channels)
    EEG_array_selected_values = np.squeeze(EEG_array[subject_id-1, 0:4, :, :, 0:14])


    # reshape x in 3d data(Trials, Channels, Samples) and y in 1d data(Trials)
    x = np.transpose(EEG_array_selected_values, (0, 1, 3, 2))
    x = x.reshape(4*33, 14, 421)
    y = [0, 1, 2, 3]
    y = np.repeat(y, 33, axis=0)

    event_dict = {"Arriba": 0, "Abajo": 1, "Izquierda": 2, "Derecha": 3}
    return x, y, event_dict

def coretto_dataset_loader(filepath: str):
    """
    Load data from all .mat files, combine them, eliminate EOG signals, shuffle and seperate
    training data, validation data and testing data. Also do mean subtraction on x.

    F3 -- Muestra 1:4096
    F4 -- Muestra 4097:8192
    C3 -- Muestra 8193:12288
    C4 -- Muestra 12289:16384
    P3 -- Muestra 16385:20480
    P4 -- Muestra 20481:24576
    Etiquetas :  Modalidad: 1 - Imaginada
	     		            2 - Pronunciada

                 Estímulo:  1 - A
                            2 - E
                            3 - I
                            4 - O
                            5 - U
                            6 - Arriba
                            7 - Abajo
                            8 - Adelante
                            9 - Atrás
                            10 - Derecha
                            11 - Izquierda
                 Artefactos: 1 - No presenta
                             2 - Presencia de parpadeo(blink)
    """

    x = []
    y = []

    #for i in np.arange(1, 10): # import all data in 9 .mat files # TODO: We are still not loading everyone. Just one subject at a time.
    EEG = loadmat(filepath) # Channels and labels are concat

    #modality = EEG['EEG'][:,24576]
    #stimulus = EEG['EEG'][:, 24577]

    direction_labels = [6, 7, 10, 11]
    EEG_filtered_by_labels = EEG['EEG'][(EEG['EEG'][:,24576] == 1) & (np.in1d(EEG['EEG'][:,24577], direction_labels))]
    x_channels_concat = EEG_filtered_by_labels[:,:-3] # Remove labels
    x_divided_in_channels = np.asarray(np.split(x_channels_concat,6,axis=1))
    # There are 3 words trials, but the samples didn't match so a series of conversions had to be done
    x_divided_in_channels_and_thirds = np.asarray(np.split(x_divided_in_channels[:,:,1:],3,axis=2))
    x_transposed = np.transpose(x_divided_in_channels_and_thirds, (1, 3, 0, 2))
    x_transposed_reshaped = x_transposed.reshape(x_transposed.shape[:-2] + (-1,))

    y = EEG_filtered_by_labels[:,-2] # Direction labels array

    # reshape x in 3d data(Trials, Channels, Samples) and y in 1d data(Trials)
    x = np.transpose(x_transposed_reshaped, (2, 0, 1))
    x = x[:,:, 0:x.shape[2]:4] # Downsampled to fs = 256Hz
    y = np.asarray(y, dtype=np.int32)
    y = np.repeat(y, 3, axis=0)

    y[y == 6] = 0
    y[y == 7] = 1
    y[y == 10] = 2
    y[y == 11] = 3
    # N, C, H = x.shape # You can use something like this for unit test later.
    event_dict = {"Arriba": 0, "Abajo": 1, "Derecha": 2, "Izquierda": 3}
    return x, y, event_dict

def load_data_labels_based_on_dataset(dataset_name: str, subject_id: int, data_path: str, transpose: bool = False, array_format: bool = True):
    if dataset_name not in ['aguilera_traditional', 'aguilera_gamified', 'nieto', 'coretto', 'torres']:
        raise Exception(
            f"Not supported dataset named '{dataset_name}', choose from the following: aguilera_traditional, aguilera_gamified, nieto, coretto or torres.")
    dataset_info = datasets_basic_infos[dataset_name]
    if 'aguilera' in dataset_name:
        filename = F"S{subject_id}.edf"
        filepath = os.path.join(data_path, filename)
        if 'gamified' in dataset_name:
            data, label = aguilera_dataset_loader(filepath, True) # The output is epochs
        else:
            data, label = aguilera_dataset_loader(filepath, False)  # The output is epochs
        if array_format:
            data = data.get_data()
    elif dataset_name == 'nieto':
        data, label, event_dict = nieto_dataset_loader(data_path, subject_id)
    elif dataset_name == 'coretto':
        foldername = "S{:02d}".format(subject_id)
        filename = foldername + "_EEG.mat"
        path = [data_path, foldername, filename]
        filepath = os.path.join(*path)
        data, label, event_dict = coretto_dataset_loader(filepath)
    elif dataset_name == 'torres':
        filename = "IndividuosS1-S7(17columnas)-Epocas.mat"
        filepath = os.path.join(data_path, filename)
        data, label, event_dict = torres_dataset_loader(filepath, subject_id)
    if transpose:
        data = np.transpose(data, (0, 2, 1))
    if not array_format and 'aguilera' not in dataset_name:
        events = np.column_stack((
            np.arange(0, dataset_info['sample_rate'] * data.shape[0], dataset_info['sample_rate']),
            np.zeros(len(label), dtype=int),
            np.array(label),
        ))
        data = EpochsArray(data, info=mne.create_info(dataset_info['#_channels'],
                                                        sfreq=dataset_info['sample_rate'], ch_types='eeg'), events=events,
                             event_id=event_dict)
    return data, label

if __name__ == '__main__':
    # Manual Inputs
    subject_id = 1  # Only two things I should be able to change
    dataset_name = 'aguilera_gamified'  # Only two things I should be able to change
    array_format = True

    # Folders and paths
    dataset_foldername = dataset_name + '_dataset'
    #computer_root_path = "/Users/rosit/Documents/MCC/voting_system_platform/Datasets/"  # OMEN
    computer_root_path = "/Users/almacuevas/work_projects/voting_system_platform/Datasets/" # MAC
    data_path = computer_root_path + dataset_foldername

    data, label = load_data_labels_based_on_dataset(dataset_name, subject_id, data_path, array_format=array_format)

    if array_format:
        print(data.shape)
        print(label.shape)
    else:
        print(data)
    print("Congrats! You were able to load data. You can now use this in a processing method.") #TODO: CHECK GAMIFIED, LABELS ARE WRONG



