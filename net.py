import torch

from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

import time
import os
from visualdl import LogWriter
import numpy as np

import utils

class Generator(nn.Module):
    def __init__(self):
        """
        3 inputs - Transform: TA->TB and Reference content C
        """
        super(Generator, self).__init__()

        self.convSet1 = nn.Sequential(
            nn.Conv2d(3, 16, 3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(),
            nn.Conv2d(16, 16, 3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU()
        )
        self.pool1 = nn.MaxPool2d(2, 2, return_indices=True)

        self.convSet2 = nn.Sequential(
            nn.Conv2d(16, 32, 3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(),
            nn.Conv2d(32, 32, 3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU()
        )
        self.pool2 = nn.MaxPool2d(2, 2, return_indices=True)

        self.convSet3 = nn.Sequential(
            nn.Conv2d(32, 32, 3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU()
        )

        self.unpool1 = nn.MaxUnpool2d(2, 2)
        #With skip connection
        self.deconvSet1 = nn.Sequential(
            nn.Conv2d(32+32, 64, 3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.Conv2d(64, 16, 3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU()
        )

        self.unpool2 = nn.MaxUnpool2d(2, 2)
        #With skip connection
        self.deconvSet2 = nn.Sequential(
            nn.Conv2d(16+1, 64, 3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.Conv2d(64, 1, 3, padding=1),
            nn.Sigmoid()
        )

    def forward(self, transformA, transformB, contentRef):
        src = torch.cat([transformA, transformB, contentRef], 1)
        x1 = self.convSet1(src)
        x2, pool1Idx = self.pool1(x1)
        x2 = self.convSet2(x2)
        x3, pool2Idx = self.pool2(x2)
        x3 = self.convSet3(x3)

        x = self.unpool1(x3, pool2Idx)
        x = torch.cat([x, x2], 1)
        x = self.deconvSet1(x)

        x = self.unpool2(x, pool1Idx)
        x = torch.cat([x, contentRef], 1)
        x = self.deconvSet2(x)

        # print('End G F')

        return x

class Discriminator(nn.Module):
    """
    3 inputs - Transform: TA->TB and something to be checked against
    """
    def __init__(self):
        super(Discriminator, self).__init__()

        self.contentDiscriminator = nn.Sequential(
            nn.Conv2d(2, 32, 4, stride=2, padding=1),
            nn.BatchNorm2d(32),
            nn.LeakyReLU(0.2),
            nn.Conv2d(32, 64, 4, stride=2, padding=1),
            nn.BatchNorm2d(64),
            nn.LeakyReLU(0.2),
            nn.Conv2d(64, 128, 4, stride=2, padding=1),
            nn.Sigmoid()
        )

        self.styleDiscriminator = nn.Sequential(
            nn.Conv2d(2, 32, 4, stride=2, padding=1),
            nn.BatchNorm2d(32),
            nn.LeakyReLU(0.2),
            nn.Conv2d(32, 64, 4, stride=2, padding=1),
            nn.BatchNorm2d(64),
            nn.LeakyReLU(0.2),
            nn.Conv2d(64, 128, 4, stride=2, padding=1),
            nn.Sigmoid()
        )

    def forward(self, transformA, transformB, target):
        contentCheck = torch.cat([transformA, target], 1)
        styleCheck = torch.cat([transformB, target], 1)

        contentScore = self.contentDiscriminator(contentCheck).mean(1).mean(1).mean(1)
        styleScore = self.styleDiscriminator(styleCheck).mean(1).mean(1).mean(1)

        return (contentScore + styleScore)/2

class Net(object):
    """
    Entire network including generator and discriminator
    """
    gpu_mode = True
    def __init__(self, data_loader, epochs, save_epoch, model_path):
        self.data_loader = data_loader
        self.epochs = epochs
        self.model_path = model_path
        self.save_epoch = save_epoch

        self.G = Generator()
        self.D = Discriminator()
        self.G_optim = optim.SGD(self.G.parameters(), lr=1e-2, momentum=0.9)
        self.D_optim = optim.SGD(self.D.parameters(), lr=1e-2, momentum=0.9)

        if self.gpu_mode:
            self.G.cuda()
            self.D.cuda()
            self.BCE_loss = nn.BCELoss().cuda()
            self.L1_Loss = nn.L1Loss().cuda()
        else:
            self.BCE_loss = nn.BCELoss()
            self.L1_Loss = nn.L1Loss()

        self.save_path = model_path + '/model_%d.weights'
        logdir = model_path + "/tmp"
        logger = LogWriter(logdir, sync_cycle=10000)

        with logger.mode("train"):
            self.log_D_real_loss = logger.scalar("D/real_loss")
            self.log_D_fake_loss = logger.scalar("D/fake_loss")
            self.log_D_total_loss = logger.scalar("D/total_loss")
            self.log_G_D_loss = logger.scalar("G/D_Loss")
            self.log_G_L1_loss = logger.scalar("G/L1_Loss")
            self.log_G_total_loss = logger.scalar("G/total_Loss")

        with logger.mode("test"):
            self.log_test_loss = logger.scalar("test/loss")

        # Print something
        # print('Generator: ')
        # print(self.G)
        # print('Discriminator: ')
        # print(self.D)

    def train(self):
        running_idx = 0
        for epoch in range(self.epochs):
            self.G.train()
            epoch_start_time = time.time()
            for i, data in enumerate(self.data_loader, 0):
                running_idx += 1
                transA, transB, reference, groundTruth = data

                if self.gpu_mode:
                    transA, transB, reference, groundTruth = Variable(transA.cuda()), \
                                                            Variable(transB.cuda()), \
                                                            Variable(reference.cuda()), \
                                                            Variable(groundTruth.cuda())

                    D_real_vector = Variable(torch.ones(transA.shape[0], 1).cuda())
                    D_fake_vector = Variable(torch.zeros(transA.shape[0], 1).cuda())
                else:
                    transA, transB, reference, groundTruth = Variable(transA), \
                                                            Variable(transB), \
                                                            Variable(reference), \
                                                            Variable(groundTruth)

                    D_real_vector = Variable(torch.ones(transA.shape[0], 1))
                    D_fake_vector = Variable(torch.zeros(transA.shape[0], 1))

                # Train discriminator
                self.D_optim.zero_grad()

                # Compute discriminator result in real data
                D_real = self.D(transA, transB, groundTruth)
                D_real_loss = self.BCE_loss(D_real, D_real_vector)

                # Generate fake data
                # print('Stage 01')
                G_out = self.G(transA, transB, reference)
                # print('Stage 02')
                D_fake = self.D(transA, transB, G_out)
                # print('Stage 03')
                D_fake_loss = self.BCE_loss(D_fake, D_fake_vector)
                # print('Stage 04')

                D_loss = D_real_loss + D_fake_loss
                # print('Stage 05')
                
                # Log result for D
                # self.log_D_real_loss.add_record(running_idx, torch.sum(D_real_loss, 0))
                # print('Stage 06')
                # self.log_D_fake_loss.add_record(running_idx, torch.sum(D_fake_loss, 0))
                # print('Stage 07')
                # self.log_D_total_loss.add_record(running_idx, torch.sum(D_loss, 0))
                # print('Stage 08')

                # Update D's parameter
                D_loss.backward()
                # print('Stage 09')
                self.D_optim.step()
                # print('Stage 10')

                # Train Generator
                self.G_optim.zero_grad()
                # print('Stage 11')
                
                # Compute Generator result
                # print('Stage 0')
                G_out = self.G(transA, transB, reference)
                # print('Stage 1')
                D_fake = self.D(transA, transB, G_out)
                # print('Stage 2')

                G_D_loss = self.BCE_loss(D_fake, D_real_vector)
                # print('Stage 3')
                G_L1_loss = self.L1_Loss(G_out, groundTruth)
                # print('Stage 4')
                G_loss = G_D_loss + G_L1_loss
                # print('Stage 5')

                num_D_fake = D_fake_loss.mean()
                num_D_real = D_real_loss.mean()
                num_D_loss = D_loss.mean()
                num_G_D_loss = G_D_loss.mean()
                num_G_L1_loss = G_L1_loss.mean()
                num_G_loss = G_loss.mean()

                # Log result for G
                # self.log_G_D_loss.add_record(running_idx, torch.mean(G_D_loss))
                # self.log_G_L1_loss.add_record(running_idx, torch.mean(G_L1_loss))
                # self.log_G_total_loss.add_record(running_idx, torch.mean(G_loss))

                G_loss.backward()
                self.G_optim.step()

                if ((running_idx + 1) % 100 == 1):
                    print('[%2d:%5d] D_loss: %.7f G_loss: %.7f' % (epoch+1, running_idx, num_D_loss, num_G_loss))
                    print('D_fake: %.7f D_real: %.7f' % (num_D_fake, num_D_real))
                    print('G_WG: %.7f G_L1: %.7f' % (num_G_D_loss, num_G_L1_loss))

                    self.visualize_results(epoch, transA, transB, reference, groundTruth, G_out)

            print('Epoch takes: %f' % (time.time() - epoch_start_time))
            if ((epoch+1) % self.save_epoch == 0):
                self.save(epoch)

    def visualize_results(self, epoch, transA, transB, reference, groundTruth, generated):
        self.G.eval()

        directory = '%s/result/epoch_%d/' % (self.model_path, epoch)
        if not os.path.exists(directory):
            os.makedirs(directory)

        image_frame_dim = int(np.floor(np.sqrt(transA.shape[0])))

        if self.gpu_mode:
            transA = transA.cpu().data.numpy().transpose(0, 2, 3, 1)
            transB = transB.cpu().data.numpy().transpose(0, 2, 3, 1)
            reference = reference.cpu().data.numpy().transpose(0, 2, 3, 1)
            groundTruth = groundTruth.cpu().data.numpy().transpose(0, 2, 3, 1)
            generated = generated.cpu().data.numpy().transpose(0, 2, 3, 1)
        else:
            transA = transA.data.numpy().transpose(0, 2, 3, 1)
            transB = transB.data.numpy().transpose(0, 2, 3, 1)
            reference = reference.data.numpy().transpose(0, 2, 3, 1)
            groundTruth = groundTruth.data.numpy().transpose(0, 2, 3, 1)
            generated = generated.data.numpy().transpose(0, 2, 3, 1)

        utils.save_images(transA[:image_frame_dim * image_frame_dim, :, :, :], [image_frame_dim, image_frame_dim],
                          directory + 'transA.png')

        utils.save_images(transB[:image_frame_dim * image_frame_dim, :, :, :], [image_frame_dim, image_frame_dim],
                          directory + 'transB.png')

        utils.save_images(reference[:image_frame_dim * image_frame_dim, :, :, :], [image_frame_dim, image_frame_dim],
                          directory + 'reference.png')
        
        utils.save_images(groundTruth[:image_frame_dim * image_frame_dim, :, :, :], [image_frame_dim, image_frame_dim],
                          directory + 'groundTruth.png')
        
        utils.save_images(generated[:image_frame_dim * image_frame_dim, :, :, :], [image_frame_dim, image_frame_dim],
                          directory + 'generated.png')

    def save(self, epoch):
        print('Saving model')
        save_dir = os.path.join(self.model_path, 'weights')

        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        torch.save(self.G.state_dict(), os.path.join(save_dir, '%d_G.pkl' % epoch))
        torch.save(self.D.state_dict(), os.path.join(save_dir, '%d_D.pkl' % epoch))

    def load(self, epoch):
        print('Loading model')
        save_dir = os.path.join(self.model_path, 'weights')

        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        self.G.load_state_dict(torch.load(os.path.join(save_dir, '%d_G.pkl' % epoch)))
        self.D.load_state_dict(torch.load(os.path.join(save_dir, '%d_D.pkl' % epoch)))