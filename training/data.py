# coding=utf-8
# Copyright 2023 The HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This file is heavily inspired by https://github.com/mlfoundations/open_clip/blob/main/src/training/data.py

import math
from typing import List, Union

import webdataset as wds
from torch.utils.data import default_collate
from torchvision import transforms


def filter_keys(key_set):
    def _f(dictionary):
        return {k: v for k, v in dictionary.items() if k in key_set}

    return _f


class ImageNetTransform:
    def __init__(self, resolution, center_crop=True, random_flip=False):
        self.train_transform = transforms.Compose(
            [
                transforms.Resize(resolution, interpolation=transforms.InterpolationMode.BILINEAR),
                (transforms.CenterCrop(resolution) if center_crop else transforms.RandomCrop(resolution)),
                transforms.RandomHorizontalFlip() if random_flip else transforms.Lambda(lambda x: x),
                transforms.ToTensor(),
            ]
        )
        self.eval_transform = transforms.Compose(
            [
                transforms.Resize(resolution, interpolation=transforms.InterpolationMode.BILINEAR),
                transforms.CenterCrop(resolution),
                transforms.ToTensor(),
            ]
        )


class ClassificationDataset:
    def __init__(
        self,
        train_shards_path_or_url: Union[str, List[str]],
        eval_shards_path_or_url: Union[str, List[str]],
        num_train_examples: int,
        per_gpu_batch_size: int,
        global_batch_size: int,
        num_workers: int,
        resolution: int = 256,
        center_crop: bool = True,
        random_flip: bool = False,
        shuffle_buffer_size: int = 1000,
        pin_memory: bool = False,
        persistent_workers: bool = False,
    ):
        transform = ImageNetTransform(resolution, center_crop, random_flip)

        # Create train dataset and loader
        pipeline = [
            wds.ResampledShards(train_shards_path_or_url),
            wds.shuffle(100),
            wds.tarfile_to_samples(handler=wds.ignore_and_continue),
            wds.shuffle(shuffle_buffer_size),
            wds.decode("pil", handler=wds.ignore_and_continue),
            wds.rename(image="jpg;png;jpeg;webp", class_id="cls"),
            wds.map(filter_keys(set(["image", "class_id"]))),
            wds.map_dict(image=transform.train_transform, class_id=lambda x: int(x)),
            wds.to_tuple("image", "class_id"),
            wds.batched(per_gpu_batch_size, partial=False, collation_fn=default_collate),
        ]

        num_batches = math.ceil(num_train_examples / global_batch_size)
        num_worker_batches = math.ceil(num_train_examples / (global_batch_size * num_workers))  # per dataloader worker
        num_batches = num_worker_batches * num_workers
        num_samples = num_batches * global_batch_size

        # each worker is iterating over this
        self._train_dataset = wds.DataPipeline(*pipeline).with_epoch(num_worker_batches)
        self._train_dataloader = wds.WebLoader(
            self._train_dataset,
            batch_size=None,
            shuffle=False,
            num_workers=num_workers,
            pin_memory=pin_memory,
            persistent_workers=persistent_workers,
        )
        # add meta-data to dataloader instance for convenience
        self._train_dataloader.num_batches = num_batches
        self._train_dataloader.num_samples = num_samples

        # Create eval dataset and loader
        pipeline = [
            wds.SimpleShardList(eval_shards_path_or_url),
            wds.split_by_worker,
            wds.tarfile_to_samples(handler=wds.ignore_and_continue),
            wds.decode("pil", handler=wds.ignore_and_continue),
            wds.rename(image="jpg;png;jpeg;webp", class_id="cls"),
            wds.map(filter_keys(set(["image", "class_id"]))),
            wds.map_dict(image=transform.eval_transform, class_id=lambda x: int(x)),
            wds.to_tuple("image", "class_id"),
            wds.batched(per_gpu_batch_size, partial=True, collation_fn=default_collate),
        ]
        self._eval_dataset = wds.DataPipeline(*pipeline)
        self._eval_dataloader = wds.WebLoader(
            self._eval_dataset,
            batch_size=None,
            shuffle=False,
            num_workers=num_workers,
            pin_memory=pin_memory,
            persistent_workers=persistent_workers,
        )

    @property
    def train_dataset(self):
        return self._train_dataset

    @property
    def train_dataloader(self):
        return self._train_dataloader

    @property
    def eval_dataset(self):
        return self._eval_dataset

    @property
    def eval_dataloader(self):
        return self._eval_dataloader
