import sys
import copy
import time
import math
import random
from datetime import datetime

import numpy as np
from numpy import linalg as la

import matplotlib.pyplot as plt

import scipy
from scipy import io, signal, fftpack
import scipy.linalg as la
import gzip
import pickle

from sklearn.model_selection import train_test_split
###Pytorch
import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable
import torch.nn.functional as F
from torch.nn.parameter import Parameter
from torch.utils.data import Dataset, DataLoader
from torch.utils.tensorboard import SummaryWriter
from torchsummary import summary
from torchvision import transforms
import matplotlib

from termcolor import colored
import wget
import mat73

import matplotlib.image as mpimg

import warnings

warnings.filterwarnings('ignore')


# set manual seed for preprocessing
def manual_seed(seed):
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    from torch.backends import cudnn
    cudnn.deterministic = True  # type: ignore
    cudnn.benchmark = False  # type: ignore


# We set the seed to 2045 because the Singularity is near!
# manual_seed(2045)

if (torch.cuda.is_available()):
    device = 'cuda'
    workers = 2
else:
    device = 'cpu'
    workers = 0
print(f'Device is set to {device}\nNumber of workers: {workers}')

from Code.data_loaders import load_data_labels_based_on_dataset
from share import datasets_basic_infos
from Code.data_utils import train_test_val_split

"""# Importing data and Preprocessing"""


class EEG_dataset(Dataset):
    def __init__(self, data, label_original, true_label=1):
        data = np.asarray([data])
        data = np.transpose(data, (1, 0, 2, 3))
        label = label_original.copy()
        for i, label_i in enumerate(label): #TODO: Fix this to work with the categorical generalized labels
            if label_i == true_label:
                label[i] = 1
            else:
                label[i] = 0
        self.section = "train"
        self.data_train, self.data_test, self.data_val, self.labels_train, self.labels_test, self.labels_val = train_test_val_split(
            dataX=data, dataY=label, valid_flag=True)

        ### Emptying Ram
        for element in [data, label]:
            del (element)
        ### Copying arrays
        self.data_train, self.labels_train = self.data_train.copy(), self.labels_train.copy()
        self.data_test, self.labels_test = self.data_test.copy(), self.labels_test.copy()

    def train(self):
        self.section = "train"

    def val(self):
        self.section = "val"

    def test(self):
        self.section = "test"

    def __len__(self):
        if self.section == 'test':
            return len(self.labels_test)
        elif self.section == 'val':
            return len(self.labels_val)
        elif self.section == 'train':
            return len(self.labels_train)

    def __getitem__(self, idx):
        if self.section == 'test':
            return self.data_test[idx], self.labels_test[idx]
        elif self.section == 'val':
            return self.data_val[idx], self.labels_val[idx]
        elif self.section == 'train':
            return self.data_train[idx], self.labels_train[idx]

class EEG_dataset_online(Dataset):
    def __init__(self, data):
        self.section = "val"
        self.data_val = np.asarray(data)
        self.labels_val = [1] # Dummy, just to not fail. It doesn't affect anything.
    def val(self):
        self.section = "val"

    def __len__(self):
        if self.section == 'val':
            return len(self.labels_val)

    def __getitem__(self, idx):
        if self.section == 'val':
            return self.data_val[idx], self.labels_val[idx]

"""# NETWORK"""


class CCSP(nn.Module):
    def __init__(self, kernLength, timepoints, nb_classes=2, wavelet_filters=4, wavelet_kernel=64, nn_layers=[4],
                 feature_reduction_network=[4], nchans=62, n_CSP_subspace_vectors=1, ):
        super(CCSP, self).__init__()
        # manual_seed(2045)
        self.T = timepoints
        self.m = n_CSP_subspace_vectors
        self.F = nn_layers[-1] if nn_layers else wavelet_filters
        self.CSP = CommonSpatialPattern(self.F, self.m, nchans)
        self.register_parameter(name='CSP projection matrix', param=self.CSP.W)
        self.CSP_fig = None
        self.kernLength = kernLength
        self.wavelet_filters = wavelet_filters
        self.wavelet_kernel = wavelet_kernel
        self.nn_layers = nn_layers
        self.batch_norm = nn.BatchNorm1d(2 * self.m * self.F)
        self.feature_reduction_network = feature_reduction_network
        ### Wawelet
        self.wavelet_padding = nn.ZeroPad2d((int(wavelet_kernel / 2) - 1, int(wavelet_kernel / 2), 0, 0))
        self.freq = torch.tensor([[4 + (i + 1) * 36 / (self.wavelet_filters + 1)] for i in range(self.wavelet_filters)],
                                 requires_grad=True, device=device)
        self.fwhm = torch.tensor([[.1] for _ in range(self.wavelet_filters)], requires_grad=True, device=device)
        self.coefficient = torch.tensor([[4 * math.log(2)] for _ in range(self.wavelet_filters)], requires_grad=True,
                                        device=device)
        ### Neural Network
        self.CNN = nn.ModuleList()
        prev_chans = wavelet_filters if wavelet_filters else 1
        for i, chans in enumerate(nn_layers):
            if i == len(nn_layers) - 1:
                last_block = True
            else:
                last_block = False
            self.CNN.append(self.CNN_block(prev_chans, chans, last_block))
            prev_chans = chans
        ### FRN
        self.FRN = nn.ModuleList()
        prev_size = self.F * 2 * self.m
        for i, size in enumerate(feature_reduction_network):
            if i == len(feature_reduction_network) - 1:
                last_block = True
            else:
                last_block = False
            self.FRN.append(self.FRN_block(prev_size, size, last_block))
            prev_size = size
        ### LDA
        if self.FRN:
            self.LDA = LinearDiscriminantAnalysis(feature_reduction_network[-1])
        else:
            self.LDA = LinearDiscriminantAnalysis(2 * self.F * self.m)
        self.register_parameter(name='LDA projection matrix', param=self.LDA.W)

    def plot_CSP(self, CSP_output, labels):
        print('CSP Plots:')
        label = labels.cpu()
        rows = math.ceil(self.F / 4)
        self.CSP_fig = plt.figure(figsize=(16, 4 * rows))
        for i in range(self.F):
            points = CSP_output[:, i, ...].squeeze(-1).cpu().detach().numpy()
            class0 = points[label == 0]
            class1 = points[label == 1]
            ax = plt.subplot(rows, 4, i + 1)
            ax.plot(class0[:, 0], class0[:, -1], 'ob')
            ax.plot(class1[:, 0], class1[:, -1], 'or', alpha=.6)
            ax.set_yticklabels([])
            ax.set_xticklabels([])
        plt.show()

    def _design_wavelet(self, kernLength, freq, fwhm, coefficient, fs=100):
        timevec = torch.arange(kernLength) / fs
        timevec = timevec - torch.mean(timevec)
        timevec = timevec.repeat(self.wavelet_filters).reshape(self.wavelet_filters, kernLength).to(device)
        csw = torch.cos(2 * math.pi * freq * timevec)
        gus = torch.exp(-(coefficient * torch.pow(timevec, 2) / torch.pow(fwhm, 2)))
        return (csw * gus).unsqueeze(1).unsqueeze(1)

    def CNN_block(self, prev_chans, chan, last_block):
        if not last_block:
            return nn.Sequential(
                nn.ZeroPad2d((int(self.kernLength / 2) - 1, int(self.kernLength / 2), 0, 0)),
                nn.Conv2d(prev_chans, chan, (1, self.kernLength), padding=0, bias=False),
                nn.Sigmoid(),
                nn.BatchNorm2d(chan),
            )
        else:
            return nn.Sequential(
                nn.ZeroPad2d((int(self.kernLength / 2) - 1, int(self.kernLength / 2), 0, 0)),
                nn.Conv2d(prev_chans, chan, (1, self.kernLength), padding=0, bias=False),
            )

    def FRN_block(self, prev_size, size, last_block):
        if not last_block:
            return nn.Sequential(
                nn.Linear(prev_size, size),
                nn.Sigmoid(),
                nn.BatchNorm1d(size),
            )
        else:
            return nn.Sequential(
                nn.Linear(prev_size, size),
            )

    def copy(self):
        state = {}
        state['wavelet'] = (copy.deepcopy(self.freq),
                            copy.deepcopy(self.fwhm),
                            copy.deepcopy(self.coefficient))
        state['state dict'] = copy.deepcopy(self.state_dict())
        state['CSP'] = copy.deepcopy((self.CSP.W.data).clone().detach().tolist())
        state['LDA'] = copy.deepcopy((self.LDA.W.data).clone().detach().tolist())
        return state

    def paste(self, state):
        self.freq, self.fwhm, self.coefficient = state['wavelet']
        self.load_state_dict(state['state dict'])
        self.CSP.W.data = torch.tensor(state['CSP'], device=device)
        self.LDA.W.data = torch.tensor(state['LDA'], device=device)

    def fit_modules(self, X, y):
        _, data = self(X)
        self.CSP.fit(data['CNN output'], y)
        self.LDA.fit(data['LDA input'], y)

    def pred(self, X, y):
        training_state = self.training
        self.training = False
        Y, _ = self(X)
        MU = torch.tensor([torch.mean(Y[y == 0]), torch.mean(Y[y == 1])]).reshape(-1, 1).to(device)
        yhat = torch.zeros(X.shape[0])
        for i, xi in enumerate(Y):
            dis0 = torch.sqrt(torch.dot(((xi - MU[0]).T), (xi - MU[0])))
            dis1 = torch.sqrt(torch.dot(((xi - MU[1]).T), (xi - MU[1])))
            if dis0 <= dis1:
                yhat[i] = 0
            else:
                yhat[i] = 1
        self.training = training_state
        return yhat.to(device)

    def forward(self, x, visualization=False):
        """
        x:   input - shape: [N, 1, NEc, Tp]
        N:   Batch size
        NEc: Number of EEG channels
        Tp:  Time point
        """
        data = {}
        ### Wavelet:
        if self.wavelet_filters:
            x = self.wavelet_padding(x)
            self.wavelet_weight = self._design_wavelet(self.wavelet_kernel, self.freq, self.fwhm, self.coefficient)
            x = F.conv2d(x, weight=self.wavelet_weight, bias=None)  # [N, F1, NEc, Tp]
        ### Network:
        for layer in self.CNN:
            x = layer(x)
        CNN_output = x  # [N, F1, NEc, Tp]
        data['CNN output'] = CNN_output
        ### CSP
        Y = self.CSP(CNN_output)  # [N, F1, 2m, Tp]
        CSP_output = torch.log(torch.var(Y, axis=-1))  # [N, F1, 2m]
        if visualization:
            self.plot_CSP(CSP_output)
        data["CSP output"] = CSP_output
        LDA_input = CSP_output.reshape((-1, 2 * self.m * self.F))  # [N, F1*2m]
        LDA_input = self.batch_norm(LDA_input)
        ### Feature Reduction Network
        if self.FRN:
            x = LDA_input
            for layer in self.FRN:
                x = layer(x)
            LDA_input = x  # [N, last NN layer size]
        ### LDA
        output = self.LDA(LDA_input)  # [N, 1]
        data['LDA input'] = LDA_input
        return output, data


"""##CSP & LDA"""


def cov(m, y=None):
    if y is not None:
        m = torch.cat((m, y), dim=0)
    m_exp = torch.mean(m, dim=1)
    x = m - m_exp[:, None]
    cov = 1 / (x.size(1) - 1) * x @ (x.T)
    return cov


#### CSP
class CommonSpatialPattern(nn.Module):
    def __init__(self, n_temporal_filters, n_subspace_vectors, NEc):
        super().__init__()
        self.F = n_temporal_filters
        self.m = n_subspace_vectors
        self.W = Parameter(torch.ones((self.F, 2 * self.m, NEc), device=device))

    def forward(self, X):
        return self.W @ X

    def fit(self, X, y):
        if type(y) != np.ndarray:
            y = y.cpu().detach().numpy()
        unique_values = np.unique(y, axis=0)
        class0 = X[[True if (i == unique_values[0]).all() else False for i in y]]  # (N_class 0 , F, NEc, Tp)
        class1 = X[[True if (i == unique_values[1]).all() else False for i in y]]  # (N_class 1 , F, NEc, Tp)
        total_W = torch.tensor([])  # (F, 2*m , NEc)
        for i in range(self.F):
            class0_i = class0[:, i, ...]  # (N_class 1 , 1, NEc, Tp)
            class1_i = class1[:, i, ...]
            class0_i = class0_i - class0_i.mean(axis=2).reshape(class0_i.shape[0], class0_i.shape[1], 1)
            class1_i = class1_i - class1_i.mean(axis=2).reshape(class1_i.shape[0], class1_i.shape[1], 1)
            RH = 0
            for x in class1_i:
                RH += ((x @ x.T) / (torch.trace(x @ x.T) + 1E-6))
            RH = RH / class1_i.shape[0]
            ####
            RL = 0
            for x in class0_i:
                RL += (x @ x.T) / (torch.trace(x @ x.T + 1E-6))
            RL = RL / class0_i.shape[0]
            RL = RL.cpu().detach().numpy()
            RH = RH.cpu().detach().numpy()
            try:
                v, u = la.eigh(RL, RH)
            except Exception as e:
                print('!', e)
                RH += np.random.random(RH.shape) * 1E-4
                RL += np.random.random(RL.shape) * 1E-4
                v, u = la.eigh(RL, RH)
            sorted_u = u[:, abs(v).argsort()[::-1]]
            W = torch.tensor(np.concatenate((sorted_u[:, :self.m], sorted_u[:, -self.m:]), axis=1))  # (NEc, 2m)
            total_W = torch.cat((total_W, W.T.unsqueeze(0)), 0)  # (F, 2m, NEc)
        self.W.data = total_W.to(device)  # (F, 2m, NEc)


###LDA
class LinearDiscriminantAnalysis(nn.Module):
    def __init__(self, input_dim):
        super().__init__()
        self.W = Parameter(torch.zeros((input_dim, 1), device=device))

    def forward(self, X):
        return (self.W.T @ X.T).T

    def fit(self, X, y):
        if type(y) != np.ndarray:
            y = y.cpu().detach().numpy()
        unique_values = np.unique(y, axis=0)
        Xc1 = (X[[True if (i == unique_values[0]).all() else False for i in y]]).T  # (N_class 0 , F, NEc, Tp)
        Xc2 = (X[[True if (i == unique_values[1]).all() else False for i in y]]).T  # (N_class 1 , F, NEc, Tp)

        mu1 = Xc1.mean(axis=1).reshape(-1, 1)
        mu2 = Xc2.mean(axis=1).reshape(-1, 1)
        Sp1 = cov(Xc1)  # 2m,  2m
        Sp2 = cov(Xc2)
        Sb = (mu1 - mu2) @ ((mu1 - mu2).T)
        Sw = Sp1 + Sp2
        Sw = Sw.cpu().detach().numpy()
        Sb = Sb.cpu().detach().numpy()
        try:
            A = np.linalg.inv(Sw) @ Sb
        except Exception as e:
            print('!', e)
            Sw += np.random.random(Sw.shape) * 1E-4
            A = np.linalg.inv(Sw) @ Sb
        u, v = la.eig(A)  # v: eigenvector , u: eigenvalue
        sorted_v = v[:, np.argsort(abs(u))[::-1]]
        ### Update W
        self.W.data = torch.tensor(sorted_v[:, 0].reshape(-1, 1)).float().to(device)


"""##Utility Functions

"""


def color(x):
    if x < 60:
        c = 'red'
    elif x < 75:
        c = 'yellow'
    else:
        c = 'green'
    return colored(x, c)


from scipy.fft import rfft, rfftfreq


def plot_fft(signal):
    yf = rfft(signal)
    xf = rfftfreq(64, 1 / 100)
    plt.plot(xf, np.abs(yf))


### Loss
def LDALoss(pred, label, alpha=0.001, epsilon=1E-5):
    def generator(X):
        return 1 / (X + 1) - 1 / 2

    pred = pred.reshape(-1).float()
    label = label.reshape(-1).float()
    return (label @ (pred) + (label - 1) @ (pred)) / len(label)


def MVLoss(pred, label):
    epsilon = 1e-5
    c0 = pred[label == 0]
    c1 = pred[label == 1]
    return (torch.var(c0) + torch.var(c1)) / ((c0.mean() - c1.mean()) ** 2 + epsilon)


def output_format(labels, m):
    label_form = lambda label, m: [1 - label] * m + [label] * m
    return torch.tensor([label_form(label, m) for label in labels]).float().to(device)


"""#Train and Evaluation

"""


def train(dataset, model, dataloader, epochs, m, validation=False, loss_ratio=.5,
          lr=0.01, wavelet_lr=0.1, criterion=MVLoss, lambda1=1e-2, lambda2=1e-1,
          early_stop_patience=None, verbose=2, tensorboard=False,
          scheduler=None):
    assert verbose in [0, 1, 2], "'vebose' can be one of 0, 1 or 2 "
    assert 0 <= loss_ratio <= 1, "'loss_ratio must be between 0 and 1"
    manual_seed(2045)
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=lambda2)
    optimizer.add_param_group({'params': model.freq, "lr": wavelet_lr * 10})
    optimizer.add_param_group({'params': model.fwhm, "lr": wavelet_lr})
    optimizer.add_param_group({'params': model.coefficient, "lr": wavelet_lr})
    # scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, factor=0.2,
    #        patience=5, min_lr=1E-5, verbose=True if verbose in [2] else False)
    batch_size = dataloader.batch_size
    n_batches = math.ceil(dataloader.dataset.data_train.shape[0] / batch_size)
    if verbose in [1, 2]:
        print('Number of Trials:', dataloader.dataset.data_train.shape[0],
              'Batch Size:', batch_size, 'Number of Batches:', n_batches)
        print('Train input shape:', dataloader.dataset.data_train.shape)
    if tensorboard:
        tb = SummaryWriter(f'runs/model01/{int(datetime.now().timestamp())}')
    train_history = {"train_loss": [], "train_accuracy": [], "model": []}
    training_start = time.time()
    dataset.train()
    all_inputs, all_labels = torch.tensor(dataloader.dataset.data_train).float().to(device), torch.tensor(
        dataloader.dataset.labels_train).to(device)
    model.fit_modules(all_inputs, all_labels)
    for epoch in range(epochs):
        if verbose in [2, 3]:
            print('=' * 70 + '\n' + f'Epoch: {epoch + 1}/ {epochs}', end='\r')
        epoch_start = time.time()
        running_loss = 0.0
        if verbose in [2, 3]:
            print(f'\n@ Batch: {1} / {n_batches}', end='\r')
        dataset.train()
        for batch, (inputs, labels) in enumerate(dataloader):
            batch_start = time.time()
            ### Wrap them in Variable
            inputs, labels = Variable(inputs).float().to(device), Variable(labels).to(device)
            ### Zero the parameter gradients
            optimizer.zero_grad()
            ###  Forward
            model.train()
            outputs, data = model(inputs)
            if False:
                rows = math.ceil(model.wavelet_filters / 4)
                plt.figure(figsize=(16, 4 * rows))
                for i in range(model.wavelet_filters):
                    ax = plt.subplot(rows, 4, i + 1)
                    plt.plot(model.wavelet_weight[i, 0, 0, :].detach().cpu())
                    ax.set_yticklabels([])
                    ax.set_xticklabels([])
                plt.show()
                plt.figure(figsize=(16, 4 * rows))
                for i in range(model.wavelet_filters):
                    ax = plt.subplot(rows, 4, i + 1)
                    plot_fft(model.wavelet_weight[i, 0, 0, :].detach().cpu().numpy())
                plt.show()
            ### CSP Loss
            CSP_output_softmax = F.softmax(data['CSP output'], dim=-1)
            CSP_loss = 0
            BCElabels = output_format(labels, m)
            for f in range(CSP_output_softmax.size(1)):
                CSP_loss += nn.BCELoss()(CSP_output_softmax[:, f, ...].float(), BCElabels)
            CSP_loss = CSP_loss / CSP_output_softmax.size(1)
            ### L1 reguralization
            all_linear1_params = torch.cat(
                [param.view(-1) for name, param in model.named_parameters() if 'weight' in name])
            l1_regularization_loss = lambda1 * torch.norm(all_linear1_params, 1)
            ### Backward + Optimize
            # formatted_labels = output_format(labels, outputs)
            loss = loss_ratio * CSP_loss + (1 - loss_ratio) * criterion(outputs, labels.type(torch.LongTensor).to(
                device)) + l1_regularization_loss
            loss.backward(retain_graph=False)
            optimizer.step()
            ### Calculate batch loss and accuracy
            running_loss += loss.item()
            predicted_labels = model.pred(inputs, labels)
            batch_accuracy = (predicted_labels == labels).sum().item() / (len(inputs))
            batch_end = time.time()
            if verbose in [2, 3]:
                print(
                    f'\033[K\r@ Batch: {batch + 1} / {n_batches} | Batch Loss: {loss.item():.4f}  |  Batch Accuracy: {batch_accuracy * 100:.2f} | Batch Duration: {time.strftime("%H:%M:%S", time.gmtime(batch_end - batch_start))}',
                    end='')
        if verbose in [2, 3]:
            print('\nCalculating Accuracies...', end='\r')
        ### Validation
        with torch.no_grad():
            ################ Update LDA & CSP
            if batch_size < len(dataloader.dataset.data_train):
                if verbose in [3]:
                    ### Evaluate model loss and accuracy before updateing CSP & LDA
                    model.eval()
                    ts_corrects = 0
                    dataset.val()
                    for batch, (test_inputs, test_labels) in enumerate(dataloader):
                        test_inputs, test_labels = Variable(test_inputs).float().to(device), Variable(test_labels).to(
                            device)
                        test_outputs, test_data = model(test_inputs)
                        CSP_output_softmax = F.softmax(test_data['CSP output'], dim=-1)
                        CSP_loss = 0
                        BCElabels = output_format(test_labels, m)
                        for f in range(CSP_output_softmax.size(1)):
                            CSP_loss += nn.BCELoss()(CSP_output_softmax[:, f, ...].float(), BCElabels)
                        CSP_loss = CSP_loss / CSP_output_softmax.size(1)
                        # formatted_labels = output_format(test_labels, test_outputs)
                        test_loss = loss_ratio * CSP_loss + (1 - loss_ratio) * criterion(test_outputs, labels.type(
                            torch.LongTensor).to(device))
                        predicted_labels = model.pred(test_inputs, test_labels).to(device)
                        ts_corrects += (predicted_labels == test_labels).sum().item()
                        ts_acc = (predicted_labels == test_labels).sum().item() / (len(test_inputs))
                        print(f'\033[Kaccuracy before updating CSP and LDA | @ Batch: {batch + 1} / | Acc = {ts_acc}',
                              end='\r')
                    total_ts_acc = (ts_corrects / len(dataloader.dataset))
                    print(
                        f'\033[Kaccuracy before updating CSP and LDA: {total_ts_acc:.4f}                                ')

                ### forward pass: Update the projection matrixes of CSP and LDA
                if verbose in [2, 3]:
                    print('\033[KForward pass', end='\r')
                model.fit_modules(all_inputs, all_labels)
                ### Calculate new train loss and accuracy
                if verbose in [2, 3]:
                    print('\rCalculating new Train Loss and Accuracy', end='\r')
                predicted_labels = model.pred(inputs, labels)
                total_tr_acc = (predicted_labels == labels).sum().item() / (len(inputs))
                total_tr_loss = loss
            else:
                total_tr_acc = batch_accuracy
                total_tr_loss = loss

            ######################
            ### Calculate test Loss & Accuracy
            if validation:
                if verbose in [2]:
                    print('\rCalculating test Loss & Accuracy', end='\r')
                model.eval()
                ts_corrects = 0
                dataset.val()
                for batch, (test_inputs, test_labels) in enumerate(dataloader):
                    test_inputs, test_labels = Variable(test_inputs).float().to(device), Variable(test_labels).to(
                        device)
                    test_outputs, test_data = model(test_inputs)
                    CSP_output_softmax = F.softmax(test_data['CSP output'], dim=-1)
                    CSP_loss = 0
                    BCElabels = output_format(test_labels, m)
                    for f in range(CSP_output_softmax.size(1)):
                        CSP_loss += nn.BCELoss()(CSP_output_softmax[:, f, ...].float(), BCElabels)
                        # print('*', f, ':',BCE.sum(axis = -1).mean().item())
                    CSP_loss = CSP_loss / CSP_output_softmax.size(1)
                    test_loss = loss_ratio * CSP_loss + (1 - loss_ratio) * criterion(test_outputs, test_labels.type(
                        torch.LongTensor).to(device))
                    # print('TEST: LDA Loss', criterion(test_outputs, test_labels) ,' , CSP Loss', CSP_loss)
                    predicted_labels = model.pred(test_inputs, test_labels).to(device)
                    ts_corrects += (predicted_labels == test_labels).sum().item()
                    ts_acc = (predicted_labels == test_labels).sum().item() / (len(test_inputs))
                    if verbose in [2]:
                        print(f'\033[KNew Test Accuracy | @ Batch: {batch + 1} / | Acc = {ts_acc}', end='\r')
                total_ts_acc = (ts_corrects / len(dataloader.dataset))
        ### TensorBoard
        if tensorboard:
            if verbose in [2, 3]:
                print('\033[KWrite on Tensorboard...', end='\r')
            for name, w in model.named_parameters():
                if 'conv' in name:
                    tb.add_histogram(name, w, i)
                    tb.add_histogram(str(name) + ' grad', w, i)
        ### Results
        train_history["train_loss"].append(running_loss / n_batches)
        train_history["train_accuracy"].append(total_tr_acc)
        if validation:
            train_history["val_loss"].append(test_loss.item())
            train_history["val_accuracy"].append(total_ts_acc)
        train_history["model"].append(model.copy())
        if early_stop_patience:
            if epoch > early_stop_patience:
                if train_history["val_loss"][- early_stop_patience] < min(
                        train_history["val_loss"][1 - early_stop_patience:]):
                    if verbose in [2, 3]:
                        print("Early stop!")
                    break
        if scheduler:
            scheduler.step(running_loss)
        epoch_end = time.time()
        if verbose in [2, 3]:
            print(
                f"\033[K ► Training Loss=  {running_loss / n_batches:.4f} |  Train Acc=  {color(round(total_tr_acc * 100, 2))} | Epoch Duration: {time.strftime('%H:%M:%S', time.gmtime(epoch_end - epoch_start))}")
        elif verbose in [1]:
            print(f"\033[K Epoch: {epoch + 1}/ {epochs}: Train Acc=  {color(round(total_tr_acc * 100, 2))}", end='\r')
    training_end = time.time()
    if verbose in [1, 2, 3]:
        print(
            f"\nFinished training!  Training duration: {time.strftime('%H:%M:%S', time.gmtime(training_end - training_start))}")
    return train_history


def evaluate(model, state, dataloader, loss_ratio, criterion, m, verbose=1):
    model.paste(state)
    with torch.no_grad():
        model.eval()
        ts_corrects = 0
        test_inputs, test_labels = torch.tensor(dataloader.dataset.data_test).float().to(device), torch.tensor(
            dataloader.dataset.labels_test).to(device)
        test_outputs, test_data = model(test_inputs)
        CSP_output_softmax = F.softmax(test_data['CSP output'], dim=-1)
        CSP_loss = 0
        BCElabels = output_format(test_labels, m)
        for f in range(CSP_output_softmax.size(1)):
            CSP_loss += nn.BCELoss()(CSP_output_softmax[:, f, ...].float(), BCElabels)
        CSP_loss = CSP_loss / CSP_output_softmax.size(1)
        test_loss = loss_ratio * CSP_loss + (1 - loss_ratio) * criterion(test_outputs,
                                                                         test_labels.type(torch.LongTensor).to(device))
        predicted_labels = model.pred(test_inputs, test_labels).to(device)
        ts_corrects += (predicted_labels == test_labels).sum().item()
        ts_acc = (predicted_labels == test_labels).sum().item() / (len(test_inputs))
    if verbose > 0:
        print((f"\033[K ► Test Loss=  {test_loss.item():.4f}  Test Acc= {color(round(ts_acc * 100, 2))}"))
    return test_loss, ts_acc

def predict_answer(model, state, dataloader):
    model.paste(state)
    with torch.no_grad():
        model.eval()
        test_inputs, test_labels = torch.tensor(dataloader.dataset.data_val).float().to(device), torch.tensor(
            dataloader.dataset.labels_val).to(device)
        predicted_labels = model.pred(test_inputs, test_labels).to(device)
    return predicted_labels

"""# Load data

Link to the preprocessed data and label:

https://drive.google.com/drive/folders/1pcskugvKgo5sCuFzAJbiK6xNO1up12JO?usp=sharing

Create a shortcut of the shared folder in your Drive, and run the following cell to load the data.
"""

# data    =  mat73.loadmat('/content/drive/MyDrive/MCC/CCSPNet/Preprocessed_Data.mat')['Data']
# data    =  np.moveaxis(data , -1, -2)
# labels  =  io.loadmat('/content/drive/MyDrive/MCC/CCSPNet/Labels.mat')['Labels']

if __name__ == '__main__':
    """#Run Network

    Mode parameter: 'SD' for subject-dependent and 'SI' for subject-independent
    """
    hists_acc = []
    hists_loss = []
    manual_seed(2045)

    dataset_name = 'aguilera'  # Only two things I should be able to change
    dataset_info = datasets_basic_infos[dataset_name]
    for i in range(1, dataset_info['subjects']):
        subject = i
        print('Subject:', i)
        dataset = EEG_dataset(dataset_name, subject=subject, mode='SD')
        ### Define Dataloader
        dataloader = DataLoader(dataset, batch_size=5300,
                                num_workers=workers, pin_memory=True,
                                shuffle=True)
        net_args = {"kernLength": 32,
                    "timepoints": 250,
                    "wavelet_filters": 4,
                    "wavelet_kernel": 64,
                    "nn_layers": [4],
                    "feature_reduction_network": [16, 8, 4],
                    "n_CSP_subspace_vectors": 2,
                    "nb_classes": dataset_info['#_class'],
                    "nchans": dataset_info['#_channels'],
                    }

        train_args = {"dataloader": dataloader,
                      "epochs": 20,
                      "lr": 0.01,
                      "wavelet_lr": 0.001,
                      "loss_ratio": 0.3,
                      "criterion": MVLoss,
                      "verbose": 0,
                      "tensorboard": False,
                      "m": net_args['n_CSP_subspace_vectors'],
                      'lambda1': 0.01,
                      'lambda2': 0.1
                      }

        net = CCSP(**net_args).to(device)

        history = train(model=net, **train_args)
        ### Evaluate
        best_model_state = history['model'][np.argmin(history['train_loss'])]
        print('The Best Epoch:', np.argmin(history['train_loss']) + 1)
        test_loss, ts_acc = evaluate(net, best_model_state, dataloader, m=net_args['n_CSP_subspace_vectors'],
                                     criterion=MVLoss,
                                     loss_ratio=train_args["loss_ratio"], verbose=1, )
        hists_acc.append([test_loss, ts_acc])

        best_model_state = history['model'][np.argmax(history['train_accuracy'])]
        print('The Best Epoch based on accuracy:', np.argmax(history['train_accuracy']) + 1)
        test_loss, ts_acc = evaluate(net, best_model_state, dataloader, m=net_args['n_CSP_subspace_vectors'],
                                     criterion=MVLoss,
                                     loss_ratio=train_args["loss_ratio"], verbose=1, )
        hists_loss.append([test_loss, ts_acc])

