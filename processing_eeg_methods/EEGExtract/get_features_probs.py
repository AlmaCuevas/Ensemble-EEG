import time

import antropy as ant
import EEGExtract.EEGExtract as eeg
import mne
import numpy
import numpy as np
import pandas as pd
from data_loaders import load_data_labels_based_on_dataset
from data_utils import (
    ClfSwitcher,
    convert_into_independent_channels,
    create_folder,
    get_best_classificator_and_test_accuracy,
    get_dataset_basic_info,
    get_input_data_path,
    standard_saving_path,
)
from scipy import signal
from scipy.stats import kurtosis, skew
from share import datasets_basic_infos
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline

#  from sklearn.feature_selection import SelectKBest, f_classif


def get_entropy(data):
    entropy_trial = []
    for data_trial in data:
        entropy_trial.append(ant.higuchi_fd(data_trial))
    return entropy_trial


def hjorth(X, D=None):
    """Compute Hjorth mobility and complexity of a time series from either two
    cases below:
        1. X, the time series of type list (default)
        2. D, a first order differential sequence of X (if D is provided,
           recommended to speed up)

    In case 1, D is computed using Numpy's Difference function.

    Notes
    -----
    To speed up, it is recommended to compute D before calling this function
    because D may also be used by other functions whereas computing it here
    again will slow down.

    Parameters
    ----------

    X
        list

        a time series

    D
        list

        first order differential sequence of a time series

    Returns
    -------

    As indicated in return line

    Hjorth mobility and complexity

    """

    if D is None:
        D = numpy.diff(X)
        D = D.tolist()

    D.insert(0, X[0])  # pad the first difference
    D = numpy.array(D)

    n = len(X)

    M2 = float(sum(D**2)) / n
    TP = sum(numpy.array(X) ** 2)
    M4 = 0
    for i in range(1, len(D)):
        M4 += (D[i] - D[i - 1]) ** 2
    M4 = M4 / n

    return numpy.sqrt(M2 / TP), numpy.sqrt(
        float(M4) * TP / M2 / M2
    )  # Hjorth Mobility and Complexity


def get_hjorth(data):
    Mobility_trial = []
    Complexity_trial = []
    for data_trial in data:
        Mobility, Complexity = hjorth(data_trial)
        Mobility_trial.append(Mobility)
        Complexity_trial.append(Complexity)
    return Mobility_trial, Complexity_trial


def get_ratio(data, Fs):
    ratio_trial = []
    for data_trial in data:
        ratio_trial.append(eeg.eegRatio(data_trial, fs=Fs))
    return ratio_trial


def get_lyapunov(data):
    lyapunov_values = []
    lyapunov_values.append(eeg.lyapunov(data))
    return lyapunov_values


def by_frequency_band(data, dataset_info: dict):
    """
    This code contains functions for feature extraction from EEG data and classification of brain activity.

    Parameters
    ----------
    data
    Gets converted from [epochs, chans, ms] to [chans x ms x epochs] # Todo: it got like 1, 2, 0: but I think it should be 0, 2, 1
    dataset_info

    Returns
    -------

    """
    frequency_ranges: dict = {
        "delta": [0, 3],
        "theta": [3, 7],
        "alpha": [7, 13],
        "beta 1": [13, 16],
        "beta 2": [16, 20],
        "beta 3": [20, 35],
        "gamma": [35, np.floor(dataset_info["sample_rate"] / 2) - 1],
    }
    features_df = get_extractions(data, dataset_info, "complete")
    for frequency_bandwidth_name, frequency_bandwidth in frequency_ranges.items():
        print(frequency_bandwidth)
        iir_params = dict(order=8, ftype="butter")
        filt = mne.filter.create_filter(
            data,
            dataset_info["sample_rate"],
            l_freq=frequency_bandwidth[0],
            h_freq=frequency_bandwidth[1],
            method="iir",
            iir_params=iir_params,
            verbose=True,
        )
        filtered = signal.sosfiltfilt(filt["sos"], data)
        filtered = filtered.astype("float64")
        features_array_ind = get_extractions(
            filtered, dataset_info, frequency_bandwidth_name
        )
        features_df = pd.concat([features_df, features_array_ind], axis=1)
    return features_df


def get_extractions(data, dataset_info: dict, frequency_bandwidth_name):
    # To use EEGExtract, the data must be [chans x ms x epochs]
    entropy_values = np.array(get_entropy(data))
    Mobility_values, Complexity_values = get_hjorth(data)
    ratio_values = np.array(get_ratio(data, dataset_info["sample_rate"])).transpose()[
        0
    ]  # α/δ Ratio
    lyapunov_values = np.array(get_lyapunov(data)[0])
    std_values = eeg.eegStd(data)
    mean_values = np.mean(data, axis=1)
    kurtosis_values = kurtosis(data, axis=1, bias=True)
    skew_values = skew(data, axis=1, bias=True)
    variance_values = np.var(data, axis=1)

    feature_array = [
        entropy_values,
        np.array(Mobility_values),
        np.array(Complexity_values),
        ratio_values,
        lyapunov_values,
        std_values,
        mean_values,
        kurtosis_values,
        skew_values,
        variance_values,
    ]

    # feature_array[np.isfinite(feature_array) == False] = 0

    column_name = [
        f"{frequency_bandwidth_name}_entropy_values",
        f"{frequency_bandwidth_name}_Mobility_values",
        f"{frequency_bandwidth_name}_Complexity_values",
        f"{frequency_bandwidth_name}_ratio",
        f"{frequency_bandwidth_name}_lyapunov",
        f"{frequency_bandwidth_name}_std_values",
        f"{frequency_bandwidth_name}_mean_values",
        f"{frequency_bandwidth_name}_kurtosis_values",
        f"{frequency_bandwidth_name}_skew_values",
        f"{frequency_bandwidth_name}_variance_values",
    ]

    feature_df = pd.DataFrame(feature_array).transpose()
    feature_df.columns = column_name

    return feature_df


def extractions_train(features_df, labels):
    # So far, it works slightly better if all features are given
    # X_SelectKBest = SelectKBest(f_classif, k=50)
    # X_new = X_SelectKBest.fit_transform(features_df, labels)
    # columns_list = X_SelectKBest.get_feature_names_out()
    # features_df = pd.DataFrame(X_new, columns=columns_list)

    classifier, acc = get_best_classificator_and_test_accuracy(
        features_df, labels, Pipeline([("clf", ClfSwitcher())])
    )
    return classifier, acc  # , columns_list


def extractions_test(clf, features_df):
    """
    This is what the real-time BCI will call.
    Parameters
    ----------
    clf : classifier trained for the specific subject
    features_df: features extracted from the data

    Returns Array of classification with 4 floats representing the target classification
    -------

    """

    array = clf.predict_proba(features_df)
    array = np.asarray([np.nanmean(array, axis=0)])  # Mean over columns
    return array


if __name__ == "__main__":
    # Manual Inputs
    datasets = [
        "braincommand"
    ]  # , 'aguilera_traditional', 'torres', 'aguilera_gamified'
    for dataset_name in datasets:
        version_name = (
            "all"  # To keep track what the output processing alteration went through
        )
        processing_name = "get_features_by_channel_by_frequency"
        data_path: str = get_input_data_path(dataset_name)
        dataset_info: dict = get_dataset_basic_info(datasets_basic_infos, dataset_name)

        create_folder(dataset_name, processing_name)
        saving_txt_path: str = standard_saving_path(
            dataset_info, processing_name, version_name
        )

        mean_accuracy_per_subject: list = []
        results_df = pd.DataFrame()

        for subject_id in range(29, 30):
            print(subject_id)
            with open(
                saving_txt_path,
                "a",
            ) as f:
                f.write(f"Subject: {subject_id}\n\n")
            epochs, data_original, labels_original = load_data_labels_based_on_dataset(
                dataset_info, subject_id, data_path
            )

            # Do cross-validation
            cv = StratifiedKFold(n_splits=10, shuffle=True, random_state=42)
            acc_over_cv = []
            testing_time_over_cv = []
            training_time = []
            accuracy = 0
            for train, test in cv.split(data_original, labels_original):
                print(
                    "******************************** Training ********************************"
                )
                start = time.time()
                data_train, labels_train = convert_into_independent_channels(
                    data_original[train], labels_original[train]
                )
                features_train = by_frequency_band(data_train, dataset_info)
                clf, accuracy = extractions_train(features_train, labels_train)
                training_time.append(time.time() - start)
                with open(
                    saving_txt_path,
                    "a",
                ) as f:
                    f.write(f"Accuracy of training: {accuracy}\n")
                print(
                    "******************************** Test ********************************"
                )
                pred_list = []
                testing_time = []

                for epoch_number in test:
                    start = time.time()
                    # Convert independent channels to pseudo-trials
                    data_test, labels_test = convert_into_independent_channels(
                        np.asarray([data_original[epoch_number]]),
                        labels_original[epoch_number],
                    )

                    probs_by_channel = []
                    for pseudo_trial in range(len(data_test)):
                        features_test = by_frequency_band(
                            np.asarray([data_test[pseudo_trial]]), dataset_info
                        )
                        array = extractions_test(clf, features_test)  # [columns_list])
                        probs_by_channel.append(array)
                    array = np.nanmean(probs_by_channel, axis=0)  # Mean over columns
                    end = time.time()

                    testing_time.append(end - start)
                    print(dataset_info["target_names"])
                    print("Probability voting system: ", array)

                    voting_system_pred = np.argmax(array)
                    pred_list.append(voting_system_pred)
                    print("Prediction: ", voting_system_pred)
                    print("Real: ", labels_original[epoch_number])

                acc = np.mean(pred_list == labels_original[test])
                testing_time_over_cv.append(np.mean(testing_time))
                acc_over_cv.append(acc)
                with open(
                    saving_txt_path,
                    "a",
                ) as f:
                    f.write(f"Prediction: {pred_list}\n")
                    f.write(f"Real label:{labels_original[test]}\n")
                    f.write(f"Mean accuracy in KFold: {acc}\n")
                print("Mean accuracy in KFold: ", acc)
            mean_acc_over_cv = np.mean(acc_over_cv)

            with open(
                saving_txt_path,
                "a",
            ) as f:
                f.write(f"Final acc: {mean_acc_over_cv}\n\n\n\n")
            print(f"Final acc: {mean_acc_over_cv}")

            temp = pd.DataFrame(
                {
                    "Subject ID": [subject_id] * len(acc_over_cv),
                    "Version": [version_name] * len(acc_over_cv),
                    "Training Accuracy": [accuracy] * len(acc_over_cv),
                    "Training Time": training_time,
                    "Testing Accuracy": acc_over_cv,
                    "Testing Time": testing_time_over_cv,
                }
            )  # The idea is that the most famous one is the one I use for this dataset
            results_df = pd.concat([results_df, temp])

        results_df.to_csv(
            standard_saving_path(
                dataset_info, processing_name, version_name, file_ending="csv"
            )
        )

    print("Congrats! The processing methods are done processing.")
