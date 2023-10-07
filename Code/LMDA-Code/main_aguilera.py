from compared_models import weights_init, MaxNormDefaultConstraint, EEGNet, ShallowConvNet
from lmda_model import LMDA
from experiment import EEGDataLoader, Experiment, setup_seed
# dataloader and preprocess
from data_loader import BCICompetition4Set2A, extract_segment_trial
from data_preprocess import preprocess4mi, mne_apply, bandpass_cnt
# tools for pytorch
from torch.utils.data import DataLoader
import torch
# tools for numpy as scipy and sys
import logging
import os
import time
import datetime
# tools for plotting confusion matrices and t-SNE
from torchsummary import summary
from Code.data_loaders import aguilera_dataset_loader, extract_segment_trial, train_test_val_split
from share import aguilera_info
# ========================= BCIIV2A data =====================================

def get_data(dataset: str, data_path: str, dataset_info: dict, valid_flag: bool = False):
    filename = F"S{subject_id}.edf"
    filepath = os.path.join(data_path, filename)

    loader = aguilera_dataset_loader(filepath)
    cnt = loader.load()

    # band-pass before segment trials
    # train_cnt = mne_apply(lambda a: a * 1e6, train_cnt)
    # test_cnt = mne_apply(lambda a: a * 1e6, test_cnt)

    cnt = mne_apply(lambda a: bandpass_cnt(a, low_cut_hz=4, high_cut_hz=38,
                                                 filt_order=200, fs=dataset_info['sample_rate'], zero_phase=False),
                          cnt)

    data, label = extract_segment_trial(cnt)
    print(data)
    label = label - 1

    data = preprocess4mi(data) # This was for MI, try it. If it doesn't work, delete it.
    x_train, x_test, x_val, y_train, y_test, y_val = train_test_val_split(dataX = data, dataY= label, valid_flag=valid_flag)

    if valid_flag:
        valid_loader = EEGDataLoader(x_val, y_val)
        valid_dl = DataLoader(valid_loader, batch_size=batch_size, shuffle=False, drop_last=False, **kwargs)
    else:
        valid_dl = None

    train_loader = EEGDataLoader(x_train, y_train)
    test_loader = EEGDataLoader(x_test, y_test)

    train_dl = DataLoader(train_loader, batch_size=batch_size, shuffle=True, drop_last=False, **kwargs)
    test_dl = DataLoader(test_loader, batch_size=batch_size, shuffle=False, drop_last=False, **kwargs)


    model_id = '%s' % share_model_name
    folder_path = './%s/%s/' % (dataset, subject_id)  # mkdir in current folder, and name it by target's num
    folder = os.path.exists(folder_path)
    if not folder:
        os.makedirs(folder_path)
    output_file = os.path.join(folder_path, '%s%s.log' % (model_id, code_num))
    fig_path = folder_path + str(model_id) + code_num  # 用来代码命名
    logging.basicConfig(
        datefmt='%Y/%m/%d %H:%M:%S',
        format="%(asctime)s %(levelname)s : %(message)s",
        level=logging.INFO,
        filename=output_file,
    )
    # log.info自带format, 因此只需要使用响应的占位符即可.
    logging.info("****************  %s for %s! ***************************", model_id, subject_id)

    if share_model_name == 'LMDA':
        Net = LMDA(num_classes=mi_class, chans=channels, samples=samples,
                   channel_depth1=model_para['channel_depth1'],
                   channel_depth2=model_para['channel_depth2'],
                   kernel=model_para['kernel'], depth=model_para['depth'],
                   ave_depth=model_para['pool_depth'], avepool=model_para['avepool'],
                   ).to(device)
        logging.info(model_para)

    elif share_model_name == 'EEGNet':
        Net = EEGNet(num_classes=mi_class, chans=channels, samples=samples).to(device)

    else:  # ConvNet
        Net = ShallowConvNet(num_classes=mi_class, chans=channels, samples=samples).to(device)

    Net.apply(weights_init)
    Net.apply(weights_init)

    logging.info(summary(Net, show_input=False))

    model_optimizer = torch.optim.AdamW(Net.parameters(), lr=lr_model)
    model_constraint = MaxNormDefaultConstraint()
    return train_dl, valid_dl, test_dl, Net, model_optimizer, model_constraint, fig_path


if __name__ == "__main__":
    start_time = time.time()
    setup_seed(521)  # 521, 322
    print('* * ' * 20)

    # basic info of the dataset
    mi_class = 4
    channels = 22
    samples = 1125
    sample_rate = 250

    # subject of the dataset
    subject_id = '1'
    device = torch.device('cuda')

    print('subject_id: ', subject_id)

    model_para = {
        'channel_depth1': 24,  # 推荐时间域的卷积层数比空间域的卷积层数更多
        'channel_depth2': 9,
        'kernel': 75,
        'depth': 9,
        'pool_depth': 1,
        'avepool': sample_rate // 10,  # 还是推荐两步pooling的
        'avgpool_step1': 1,
    }

    share_model_name = 'LMDA'
    assert share_model_name in ['LMDA', 'EEGNet', 'ConvNet']

    today = datetime.date.today().strftime('%m%d')
    if share_model_name == 'LMDA':

        code_num = 'D{depth}_D{depth1}_D{depth2}_pool{pldp}'.format(depth=model_para['depth'],
                                                                    depth1=model_para['channel_depth1'],
                                                                    depth2=model_para['channel_depth2'],
                                                                    pldp=model_para['avepool'] * model_para[
                                                                        'avgpool_step1'])
    else:
        code_num = ''
    print(share_model_name + code_num)
    print(device)
    print(model_para)
    print('* * ' * 20)

    # ===============================  超 参 数 设 置 ================================
    lr_model = 1e-3
    step_one_epochs = 300
    batch_size = 32
    kwargs = {'num_workers': 1, 'pin_memory': True}  # 因为pycharm开启了多进行运行main, num_works设置多个会报错
    # ================================================================================
    dataset = 'aguilera_dataset'
    data_path = "/Users/almacuevas/work_projects/voting_system_platform/Datasets/aguilera_dataset"

    train_dl, valid_dl, test_dl, ShallowNet, model_optimizer, model_constraint, fig_path = get_data(dataset, data_path, aguilera_info)

    exp = Experiment(model=ShallowNet,
                     device=device,
                     optimizer=model_optimizer,
                     train_dl=train_dl,
                     test_dl=test_dl,
                     val_dl=valid_dl,
                     fig_path=fig_path,
                     model_constraint=model_constraint,
                     step_one=step_one_epochs,
                     classes=mi_class,
                     )
    exp.run()

    end_time = time.time()
    # print('Net channel weight:', ShallowNet.channel_weight)
    logging.info('Param again {}'.format(model_para))
    logging.info('Done! Running time %.5f', end_time - start_time)
