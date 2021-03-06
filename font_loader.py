import torch
import torchvision
from torch.utils.data import Dataset, DataLoader
import numpy as np

import os
import random
from font_reader import FontReader
import collections

def PILGrayArrayToTensor(PILImageList):
    return np.concatenate([PILGrayToTensor(x) for x in PILImageList], axis=0)

def PILGrayToTensor(PILImage):
    """
    Defining this myself since torch.transform is weak
    """
    npArray = np.array(PILImage).astype('float32') / 255.0
    return npArray[None, :, :]

"""
Load pairs of font for pytorch
"""
class FontDataset(Dataset):

    def __init__(self, font_root, char_list, std_root, font_size, image_size, numTransform, numRef, forceChar=None):
        self.font_root = font_root
        self.font_reader_list = [FontReader(os.path.join(font_root, name), font_size, image_size)
                                 for name in os.listdir(font_root)]

        self.std_reader_list = [FontReader(os.path.join(std_root, name), font_size, image_size)
                                 for name in os.listdir(std_root)]

        self.numTransform = numTransform
        self.forceChar = forceChar

        assert(len(self.std_reader_list) == numRef)

        # Remove characters that don't exist in one of the fonts
        # Using hash to approximate
        for reader in self.font_reader_list + self.std_reader_list:
            # Get a hash value that appears more than a certain threshold -- let's say 3
            recur_hash = 0
            hash_dict = collections.defaultdict(int)
            for char in char_list:
                img = reader.get_image(char)
                hash_val = hash(img.tobytes())
                hash_dict[hash_val] += 1
                if (hash_dict[hash_val] >= 3):
                    recur_hash = hash_val
                    break
            # Remove all characters with such hash value
            if (recur_hash != 0):
                char_list = [x for x in char_list if hash(reader.get_image(x).tobytes()) != recur_hash]
            print('Remaining characters: %d' % (len(char_list)))

        self.char_list = char_list

    def __len__(self):
        if self.forceChar is not None:
            return 1
        return len(self.font_reader_list) * len(self.char_list)

    def __getitem__(self, idx):
        char_length = len(self.char_list)

        char_idx = idx %  char_length
        font_idx = idx // char_length

        #transA = self.numTransform*[None]
        if self.forceChar is None:
            tar_char = self.char_list[char_idx]
        else:
            tar_char = self.forceChar
        transB = self.numTransform*[None]
        for i in range(self.numTransform):
            ref_char = random.choice(self.char_list)

            # Generate reference transform
            #transA[i] = (self.std_reader_list[0].get_image(ref_char))
            transB[i] = (self.font_reader_list[font_idx].get_image(ref_char))

        # Generate content reference
        conRef = [x.get_image(tar_char) for x in self.std_reader_list]
        # Generate ground truth
        genGT = self.font_reader_list[font_idx].get_image(tar_char)

        #transA = PILGrayArrayToTensor(transA)
        transB = PILGrayArrayToTensor(transB)
        conRef = PILGrayArrayToTensor(conRef)
        genGT = PILGrayToTensor(genGT)

        # numpy image: H x W x C
        # torch image: C X H X W

        return [], transB, conRef, genGT