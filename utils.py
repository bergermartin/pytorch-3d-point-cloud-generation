"""Utilities for 2D-3D conversion, training and building models"""
import os
import logging

import pandas as pd
import torch
from torch import optim
from torch import nn
from torch.optim import lr_scheduler
from torch.utils.data import DataLoader
from tensorboardX import SummaryWriter

import data
from torchvision import utils as vutils
from PCGModel import Structure_Generator


def projection(Vs, Vt):
    '''compute projection from source to target'''
    VsN = Vs.size(0)
    VtN = Vt.size(0)
    Vt_rep = torch.repeat(Vt[None, :, :], [VsN, 1, 1])  # [VsN,VtN,3]
    Vs_rep = torch.repeat(Vs[:, None, :], [1, VtN, 1])  # [VsN,VtN,3]
    diff = Vt_rep - Vs_rep
    dist = torch.sqrt(torch.reduce_sum(diff**2, axis=[2]))  # [VsN,VtN]
    idx = torch.to_int32(torch.argmin(dist, axis=1))
    proj = torch.gather_nd(Vt_rep, torch.stack([torch.range(VsN), idx], axis=1))
    minDist = torch.gather_nd(dist, torch.stack([torch.range(VsN), idx], axis=1))

    return proj, minDist

def make_folder(PATH):
    if not os.path.exists(PATH):
        os.makedirs(PATH)

def make_logger(PATH):
    logger = logging.getLogger("logger")
    logger.setLevel(logging.DEBUG)
    if not logger.hasHandlers():
        logger.addHandler(logging.FileHandler(filename=f"{PATH}.log"))
    
    print("Create logger")
    return logger

def make_summary_writer(EXPERIMENT):
    writer = SummaryWriter(comment="_"+EXPERIMENT)

    print("Create tensorboard logger")
    return writer


def make_data_fixed(cfg):
    ds_tr = data.PointCloud2dDataset(
        cfg, loadNovel=False, loadFixedOut=True, loadTest=False)
    dl_tr = DataLoader(
        ds_tr, batch_size=cfg.chunkSize, shuffle=True,
        drop_last=True, collate_fn=ds_tr.collate_fn_fixed, num_workers=4)

    ds_test = data.PointCloud2dDataset(
        cfg, loadNovel=False, loadFixedOut=True, loadTest=True)
    dl_test = DataLoader(
        ds_test, batch_size=cfg.chunkSize, shuffle=False,
        drop_last=True, collate_fn=ds_test.collate_fn_fixed, num_workers=4)

    print(f"Load fixed (stg1) data for category: {cfg.category}")
    print(f"batch size:{cfg.batchSize}, chunk size: {cfg.chunkSize}")
    return dl_tr, dl_test

def unpack_batch_fixed(batch, device):
    input_images = batch['inputImage'].float().to(device)
    depthGT = batch['depthGT'].float().to(device)
    maskGT = batch['maskGT'].float().to(device)

    return input_images, depthGT, maskGT

def make_data_novel(cfg):
    ds_tr = data.PointCloud2dDataset(
        cfg, loadNovel=True, loadFixedOut=False, loadTest=False)
    dl_tr = DataLoader(
        ds_tr, batch_size=cfg.chunkSize, shuffle=True,
        drop_last=True, collate_fn=ds_tr.collate_fn)

    ds_test = data.PointCloud2dDataset(
        cfg, loadNovel=True, loadFixedOut=False, loadTest=True)
    dl_test = DataLoader(
        ds_test, batch_size=cfg.chunkSize, shuffle=False,
        drop_last=True, collate_fn=ds_test.collate_fn)

    print(f"Load novel (stg2) data for category: {cfg.category}")
    print(f"batch size:{cfg.batchSize}, chunk size: {cfg.chunkSize}")
    return dl_tr, dl_test

def unpack_batch_novel(batch, device):
    input_images = batch['inputImage'].float().to(device)
    renderTrans = batch['targetTrans'].float().to(device)
    depthGT = batch['depthGT'].float().to(device)
    maskGT = batch['maskGT'].float().to(device)

    return input_images, renderTrans, depthGT, maskGT

def define_losses():
    l1_loss = nn.L1Loss()
    bce_loss = nn.BCEWithLogitsLoss()
    return l1_loss, bce_loss

def build_structure_generator(cfg):
    model = Structure_Generator(outViewN=cfg.outViewN, outW=cfg.outW, outH=cfg.outH, renderDepth=cfg.renderDepth)
    statement = "Build Structure Generator"

    if cfg.loadPath is not None:
        LOAD_PATH = f"models/{cfg.loadPath}"

        if cfg.loadEpoch is None:
            model.load_state_dict(torch.load(f"{LOAD_PATH}/best.pth"))
            statement += f"and load best weights from {LOAD_PATH}"
        else:
            model.load_state_dict(
                torch.load(f"{LOAD_PATH}/{cfg.loadEpoch}.pth"))
            statement += f"and load weights epoch {cfg.loadEpoch} from {LOAD_PATH}"

    print(statement)
    return model

def make_optimizer(cfg, model):
    params = model.parameters()

    if cfg.trueWD is not None:
        statement = "Use true (decouple with L2 regularization) weight decay "
        if cfg.optim.lower() in 'adam':
            statement += "with Adam optimizer (AdamW)"
            opt = optim.Adam(params, cfg.lr, weight_decay=0)
        elif cfg.optim.lower() in 'sgd':
            statement += "with SGD optimizer (SGDW)"
            opt = optim.SGD(params, cfg.lr)
        statement += f"\nLearning rate: {cfg.lr:.2e}, weight decay: {cfg.trueWD:.2e}"
    else:
        statement = "Use default (coupled with L2 regularization) weight decay "
        if cfg.optim.lower() in 'adam':
            statement += "with Adam optimizer (Adam)"
            opt = optim.Adam(params, cfg.lr, weight_decay=cfg.wd)
        elif cfg.optim.lower() in 'sgd':
            statement += "with SGD optimizer (SGD)"
            opt = optim.SGD(params, cfg.lr, weight_decay=cfg.wd)
        statement += f"\nLearning rate: {cfg.lr:.2e}, weight decay: {cfg.wd:.2e}"

    print(statement)
    return opt

def make_lr_scheduler(cfg, optimizer):
    if not cfg.lrSched:
        return None
    elif cfg.lrSched.lower() in 'step':
        statement = f'Using StepLR with gamma: {cfg.lrGamma:.2e} and step size: {cfg.lrStep}'
        sched = lr_scheduler.StepLR(optimizer, cfg.lrStep, cfg.lrGamma)
    elif cfg.lrSched.lower() in 'exponential':
        statement = f'Using ExponentialLR with gamma: {cfg.lrGamma:.2e}'
        sched = lr_scheduler.ExponentialLR(optimizer, cfg.lrGamma)
    elif cfg.lrSched.lower() in 'cosine':
        statement = f'Using CosineAnnealingLR with T_max: {cfg.TMax}'
        sched = lr_scheduler.CosineAnnealingLR(optimizer, cfg.TMax, cfg.etaMin)

    print(statement)
    return sched


def save_best_model(model_path, model, df_hist):
    if df_hist['val_loss'].tail(1).iloc[0] <= df_hist['val_loss'].min():
        torch.save(model.state_dict(), f"{model_path}/best.pth")

def checkpoint_model(model_path, model, epoch, saveEpoch):
    if (saveEpoch is not None) and ((epoch+1) % saveEpoch == 0):
        torch.save(model.state_dict(), f"{model_path}/{epoch}.pth")


def log_hist(logger, df_hist):
    last = df_hist.tail(1)
    best = df_hist.sort_values('val_loss').head(1)
    summary = pd.concat((last, best)).reset_index(drop=True)
    summary['name'] = ['Last', 'Best']
    logger.debug(summary[['name', 'epoch', 'train_loss', 'val_loss']])
    logger.debug('')

def write_on_board_losses_stg1(writer, df_hist):
    row = df_hist.tail(1).iloc[0]

    writer.add_scalars('loss', {
        'train': row.train_loss,
        'val': row.val_loss,
    }, row.epoch)
    writer.add_scalars('loss_XYZ', {
        'train': row.train_loss_XYZ,
        'val': row.val_loss_XYZ,
    }, row.epoch)
    writer.add_scalars('loss_mask', {
        'train': row.train_loss_mask,
        'val': row.val_loss_mask,
    }, row.epoch)

def write_on_board_losses_stg2(writer, df_hist):
    row = df_hist.tail(1).iloc[0]

    writer.add_scalars('loss', {
        'train': row.train_loss,
        'val': row.val_loss,
    }, row.epoch)
    writer.add_scalars('loss_depth', {
        'train': row.train_loss_depth,
        'val': row.val_loss_depth,
    }, row.epoch)
    writer.add_scalars('loss_mask', {
        'train': row.train_loss_mask,
        'val': row.val_loss_mask,
    }, row.epoch)

def write_on_board_images_stg1(writer, images, epoch):
    writer.add_image('RGB', images['RGB'], epoch)
    writer.add_image('depth/GT', images['depthGT'], epoch)
    writer.add_image('depth/pred', images['depth'], epoch)
    writer.add_image('mask/GT', images['maskGT'], epoch)
    writer.add_image('mask/pred', images['mask'], epoch)
    writer.add_image('depth*mask', images['depth_mask'], epoch)

def write_on_board_images_stg2(writer, images, epoch):
    writer.add_image('RGB', images['RGB'], epoch)
    writer.add_image('depth/GT', images['depthGT'], epoch)
    writer.add_image('depth/pred', images['depth'], epoch)
    writer.add_image('mask/GT', images['maskGT'], epoch)
    writer.add_image('mask/pred', images['mask'], epoch)
    writer.add_image('mask/rendered', images['mask_rendered'], epoch)

def write_on_board_lr(writer, lr, epoch):
    if lr is not None: writer.add_scalar('learning rate', lr, epoch)

def make_grid(t):
    return vutils.make_grid(t, normalize=True)
