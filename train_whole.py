from docopt import docopt
import sys
from os.path import dirname, join
from tqdm import tqdm, trange
from datetime import datetime

import pickle

import torch
from torch.autograd import Variable
from torch.utils.data import Dataset, DataLoader
from torch.utils import data as data_utils
from torch import nn
from torch import optim
import torch.backends.cudnn as cudnn
from torch.utils import data as data_utils
from torch.utils.data.sampler import Sampler
import numpy as np
from numba import jit


from utils import generate_cloned_samples, Speech_Dataset
import dv3

import sys
import os

# sys.path.append('./deepvoice3_pytorch')
from dv3 import build_deepvoice_3
from SpeechEmbedding import Encoder

# print(hparams)
batch_size_encoder = 16


global_step = 0
global_epoch = 0
use_cuda = torch.cuda.is_available()
if use_cuda:
    cudnn.benchmark = False

"""
def train(model_dv3,model_encoder
            data_loader_dv3,
            optimizer_dv3,
            init_lr_dv3=0.002,
            checkpoint_dir_dv3=None,
            clip_thresh = 1.0,
            data_loader_encoder=None,
            optimizer_encoder=None,
            scheduler_encoder=None,
            checkpoint_interval=None,
            nepochs=None):
    # this training function is to train the combined model

    grad = {}
    def save_grad(name):
        def hook(grad):
            grads[name] = grad
        return hook

    # to remember the embeddings of the speakers
    model_dv3.embed_speakers.weight.register_hook(save_grad('embeddings'))

    if use_cuda:
        model_dv3 = model_dv3.cuda()
        model_encoder = model_encoder.cuda()
    linear_dim = model_dv3.linear_dim
    r = hparams.outputs_per_step
    downsample_step = hparams.downsample_step
    current_lr = init_lr_dv3

    binary_criterion_dv3 = nn.BCELoss()

    global global_step, global_epoch
    while global_epoch < nepochs:
        running_loss = 0.0
        for step, (x, input_lengths, mel, y, positions, done, target_lengths,
                   speaker_ids) \
                in tqdm(enumerate(data_loader_dv3)):


            model_dv3.zero_grad()
            encoder.zero_grad()

            #Declaring Requirements
            model_dv3.train()
            ismultispeaker = speaker_ids is not None
            # Learning rate schedule
            if hparams.lr_schedule is not None:
                lr_schedule_f = getattr(dv3.lrschedule, hparams.lr_schedule)
                current_lr = lr_schedule_f(
                    init_lr, global_step, **hparams.lr_schedule_kwargs)
                for param_group in optimizer.param_groups:
                    param_group['lr'] = current_lr
            optimizer_dv3.zero_grad()

            # Used for Position encoding
            text_positions, frame_positions = positions

            # Downsample mel spectrogram
            if downsample_step > 1:
                mel = mel[:, 0::downsample_step, :].contiguous()

            # Lengths
            input_lengths = input_lengths.long().numpy()
            decoder_lengths = target_lengths.long().numpy() // r // downsample_step

            voice_encoder = mel.view(mel.shape[0],1,mel.shape[1],mel.shape[2])
            # Feed data
            x, mel, y = Variable(x), Variable(mel), Variable(y)
            voice_encoder = Variable(voice_encoder)
            text_positions = Variable(text_positions)
            frame_positions = Variable(frame_positions)
            done = Variable(done)
            target_lengths = Variable(target_lengths)
            speaker_ids = Variable(speaker_ids) if ismultispeaker else None
            if use_cuda:
                x = x.cuda()
                text_positions = text_positions.cuda()
                frame_positions = frame_positions.cuda()
                y = y.cuda()
                mel = mel.cuda()
                voice_encoder = voice_encoder.cuda()
                done, target_lengths = done.cuda(), target_lengths.cuda()
                speaker_ids = speaker_ids.cuda() if ismultispeaker else None

            # Create mask if we use masked loss
            if hparams.masked_loss_weight > 0:
                # decoder output domain mask
                decoder_target_mask = sequence_mask(
                    target_lengths / (r * downsample_step),
                    max_len=mel.size(1)).unsqueeze(-1)
                if downsample_step > 1:
                    # spectrogram-domain mask
                    target_mask = sequence_mask(
                        target_lengths, max_len=y.size(1)).unsqueeze(-1)
                else:
                    target_mask = decoder_target_mask
                # shift mask
                decoder_target_mask = decoder_target_mask[:, r:, :]
                target_mask = target_mask[:, r:, :]
            else:
                decoder_target_mask, target_mask = None, None

            #apply encoder model



            model_dv3.embed_speakers.weight.data = (encoder_out).data
            # Apply dv3 model
            mel_outputs, linear_outputs, attn, done_hat = model_dv3(
                    x, mel, speaker_ids=speaker_ids,
                    text_positions=text_positions, frame_positions=frame_positions,
                    input_lengths=input_lengths)



            # Losses
            w = hparams.binary_divergence_weight

            # mel:
            mel_l1_loss, mel_binary_div = spec_loss(
                    mel_outputs[:, :-r, :], mel[:, r:, :], decoder_target_mask)
                mel_loss = (1 - w) * mel_l1_loss + w * mel_binary_div

            # done:
            done_loss = binary_criterion(done_hat, done)

            # linear:
            n_priority_freq = int(hparams.priority_freq / (fs * 0.5) * linear_dim)
                linear_l1_loss, linear_binary_div = spec_loss(
                    linear_outputs[:, :-r, :], y[:, r:, :], target_mask,
                    priority_bin=n_priority_freq,
                    priority_w=hparams.priority_freq_weight)
                linear_loss = (1 - w) * linear_l1_loss + w * linear_binary_div

            # Combine losses
            loss_dv3 = mel_loss + linear_loss + done_loss
            loss_dv3 = mel_loss + done_loss
            loss_dv3 = linear_loss

            # attention
            if hparams.use_guided_attention:
                soft_mask = guided_attentions(input_lengths, decoder_lengths,
                                              attn.size(-2),
                                              g=hparams.guided_attention_sigma)
                soft_mask = Variable(torch.from_numpy(soft_mask))
                soft_mask = soft_mask.cuda() if use_cuda else soft_mask
                attn_loss = (attn * soft_mask).mean()
                loss_dv3 += attn_loss

            if global_step > 0 and global_step % checkpoint_interval == 0:
                save_states_dv3(
                    global_step, writer, mel_outputs, linear_outputs, attn,
                    mel, y, input_lengths, checkpoint_dir)
                save_checkpoint_dv3(
                    model, optimizer, global_step, checkpoint_dir, global_epoch,
                    train_seq2seq, train_postnet)

            if global_step > 0 and global_step % hparams.eval_interval == 0:
                eval_model(global_step, writer, model, checkpoint_dir, ismultispeaker)

            # Update
            loss_dv3.backward()
            encoder_out.backward(grads['embeddings'])

            optimizer_dv3.step()
            optimizer_encoder.step()

            # if clip_thresh> 0:
            #     grad_norm = torch.nn.utils.clip_grad_norm(
            #         model.get_trainable_parameters(), clip_thresh)
            global_step += 1
            running_loss += loss.data[0]

        averaged_loss = running_loss / (len(data_loader))

        print("Loss: {}".format(running_loss / (len(data_loader))))

        global_epoch += 1


    # dv3 loss function
    # backward on that
    mel_outputs.backward()
    # dv3_model.embed_speakers.weight.data = (encoder_out).data
======="""

def get_cloned_voices(model,no_speakers = 108,no_cloned_texts = 23):
    try:
        with open("./Cloning_Audio/speakers_cloned_voices_mel.p" , "rb") as fp:
            cloned_voices = pickle.load(fp)
    except:
        cloned_voices = generate_cloned_samples(model)
    if(np.array(cloned_voices).shape != (no_speakers , no_cloned_texts)):
        cloned_voices = generate_cloned_samples(model,"./Cloning_Audio/cloning_text.txt" ,no_speakers,True,0)
    print("Cloned_voices Loaded!")
    return cloned_voices

# Assumes that only Deep Voice 3 is given
def get_speaker_embeddings(model):
    '''
        return the speaker embeddings and its shape from deep voice 3
    '''
    embed = model.embed_speakers.weight.data
    # shape = embed.shape
    return embed

def build_encoder():
    encoder = Encoder()
    return encoder


def save_checkpoint(model, optimizer, checkpoint_path, epoch):

    optimizer_state = optimizer.state_dict()
    torch.save({
        "state_dict": model.state_dict(),
        "optimizer": optimizer_state,
        "global_epoch": epoch,
        "epoch":epoch+1,

    }, checkpoint_path)
    print("Saved checkpoint:", checkpoint_path)

def load_checkpoint(encoder, optimizer, path='checkpoints/encoder_checkpoint.pth'):

    checkpoint = torch.load(path)

    encoder.load_state_dict(checkpoint["state_dict"])

    print('Encoder state restored')

    optimizer.load_state_dict(checkpoint["optimizer"])

    print('Optimizer state restored')

    return encoder, optimizer

def my_collate(batch):
    data = [item[0] for item in batch]
    samples = [text.shape[0] for text in data]
    max_size = data[0].shape[1]
    max_samples = np.amax(np.array(samples))
    for i, i_element in enumerate(data):
        final = torch.zeros(int(max_samples), max_size, 80)
        final[:data[i].shape[0], :, :] += torch.from_numpy(i_element).type(torch.FloatTensor)
        data[i]=torch.unsqueeze(final, 0)
    data = torch.cat(data, 0)
    target = np.stack([item[1] for item in batch], 0)
    target = torch.from_numpy(target)
    return [data, target]

def train_encoder(encoder, data, optimizer, scheduler, criterion, epochs=100000, after_epoch_download=1000):

    #scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.6)

    for i in range(epochs):

        epoch_loss=0.0

        for i_element, element in enumerate(data):

            voice, embed = element[0], element[1]

            input_to_encoder = Variable(voice.type(torch.cuda.FloatTensor))

            optimizer.zero_grad()

            output_from_encoder = encoder(input_to_encoder)

            embeddings = Variable(embed.type(torch.cuda.FloatTensor))

            loss = criterion(output_from_encoder,embeddings)

            loss.backward()

            scheduler.step()
            optimizer.step()

            epoch_loss+=loss


        if i%100==99:
            save_checkpoint(encoder,optimizer,"encoder_checkpoint.pth",i)
            print(i, ' done')
            print('Loss for epoch ', i, ' is ', loss)

def download_file(file_name=None):
    from google.colab import files
    files.download(file_name)


batch_size=64

if __name__ == "__main__":

    #Load Deep Voice 3
    # Pre Trained Model
    dv3_model = build_deepvoice_3(True)

    all_speakers = get_cloned_voices(dv3_model)
    print("Cloning Texts are produced")

    speaker_embed = get_speaker_embeddings(dv3_model)

    encoder = build_encoder()

    print("Encoder is built!")


    speech_data = Speech_Dataset(all_speakers, speaker_embed, sampler=True)

    criterion = nn.L1Loss()

    optimizer = torch.optim.SGD(encoder.parameters(),lr=0.0006)

    lambda1 = lambda epoch: 0.6 if epoch%8000==7999 else 1
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lambda1)


    data_loader = DataLoader(speech_data, batch_size=batch_size, shuffle=True, drop_last=True, collate_fn = my_collate)
    # Training The Encoder

    encoder = encoder.cuda()

    if os.path.isfile('checkpoints/encoder_checkpoint.pth'):
        encoder, optimizer = load_checkpoint(encoder, optimizer)
    
    try:
        train_encoder(encoder, data_loader, optimizer, scheduler, criterion, epochs=100000)
    except KeyboardInterrupt:
        print("KeyboardInterrupt")

    print("Finished")
    sys.exit(0)
