from share import datasets_basic_infos
from data_loaders import load_data_labels_based_on_dataset
import time

from tcacnet_base import EEGdata_loader, call_tcanet, tcanet_online_pred, CustomDataset
import torch
from torch.utils.data import DataLoader
import numpy as np

def tcanet_train(dataset_name, subject_id, data, labels, channels, ONLY_GLOBAL_MODEL):
    train_loader, valid_loader, test_loader = EEGdata_loader(data, labels)
    model_global, model_local, model_top, acc = call_tcanet(dataset_name, subject_id, train_loader, valid_loader, test_loader, channels=channels, only_global_model=ONLY_GLOBAL_MODEL) # There are 2 models here, use False to access the second version
    if acc <= 0.25:
        acc = np.nan
    return model_global, model_local, model_top, acc

def tcanet_test(dataset_name, subject_id, model_global, model_local, model_top, trial_data, channels, ONLY_GLOBAL_MODEL):
    """
    This is what the real-time BCI will call.
    Parameters
    ----------
    ONLY_GLOBAL_MODEL
    clf : classifier trained for the specific subject
    trial_data: one trial, the current one that represents the intention of movement of the user.

    Returns Array of classification with 4 floats representing the target classification
    -------

    """
    # To load the model, just in case
    #loaded_model = pickle.load(open(filename, 'rb'))

    # To see the array of predictions
    trial_data = CustomDataset(trial_data, [0]) # The label is dummy, it's just for the code to work
    use_cuda = False
    loader = DataLoader(trial_data, pin_memory=use_cuda)

    model_global.load_state_dict(torch.load(f'model_global_cross_entropy_{dataset_name}_{subject_id}.pth'))
    model_local.load_state_dict(torch.load(f'model_local_cross_entropy_{dataset_name}_{subject_id}.pth'))
    model_top.load_state_dict(torch.load(f'model_top_cross_entropy_{dataset_name}_{subject_id}.pth'))

    output_array = tcanet_online_pred(model_global, model_local, model_top, loader, channels, only_global_model=ONLY_GLOBAL_MODEL)
    return output_array

if __name__ == '__main__':
    # Manual Inputs
    subject_id = 1  # Only two things I should be able to change
    dataset_name = 'aguilera_traditional'  # Only two things I should be able to change
    ONLY_GLOBAL_MODEL = True

    array_format = True

    # Folders and paths
    dataset_foldername = dataset_name + '_dataset'
    # computer_root_path = "/Users/rosit/Documents/MCC/voting_system_platform/Datasets/"  # OMEN
    computer_root_path = "//"  # MAC
    data_path = computer_root_path + dataset_foldername
    dataset_info = datasets_basic_infos[dataset_name]

    data, label = load_data_labels_based_on_dataset(dataset_name, subject_id, data_path, array_format=array_format)
    target_names = dataset_info['target_names']

    if torch.cuda.is_available():
        device = torch.device("cuda")
        use_cuda = True
        print('Using cuda !')
    else:
        device = torch.device("cpu")
        use_cuda = False
        print('GPU not available !')

    print("******************************** Training ********************************")
    start = time.time()
    model_global, model_local, model_top = tcanet_train(dataset_name, subject_id, data, label, dataset_info['#_channels'], ONLY_GLOBAL_MODEL)
    end = time.time()
    print("Training time: ", end - start)

    print("******************************** Test ********************************")
    epoch_number = [0]
    start = time.time()
    array = tcanet_test(dataset_name, subject_id, model_global, model_local, model_top, data[epoch_number],
                        dataset_info['#_channels'], ONLY_GLOBAL_MODEL)
    end = time.time()
    print("One epoch, testing time: ", end - start)
    print(target_names)
    print("Probability: " , array[0]) # We select the last one, the last epoch which is the current one.
    print("Real: ", label[0])