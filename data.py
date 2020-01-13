import concurrent.futures
from collections import defaultdict

import numpy as np
import torch
import tqdm
from PIL import ImageFile
from torch.utils.data import Dataset

from utils.dataset_tools import get_label_set, load_dataset, load_image

ImageFile.LOAD_TRUNCATED_IMAGES = True


def augment_image(image, transforms):
    for transform_current in transforms:
        image = transform_current(image)

    return image


class AddChannelsToTensor(object):
    """Convert a ``PIL Image`` or ``numpy.ndarray`` to tensor.

    Converts a single-channel image into a three-channel image, by cloning the original channel three times.

    In the other cases, tensors are returned without changes.
    """

    def __call__(self, pic):
        """
        Args:
            pic (PIL Image or numpy.ndarray): Image to be converted to tensor.

        Returns:
            Tensor: Converted image.
        """
        return pic if pic.shape[0] == 3 else pic.repeat([3, 1, 1])


class FewShotLearningDatasetParallel(Dataset):
    def __init__(self, dataset_path, dataset_name, indexes_of_folders_indicating_class, train_val_test_split,
                 labels_as_int, transforms, num_classes_per_set, num_continual_subtasks_per_task,
                 num_samples_per_support_class, num_channels,
                 num_samples_per_target_class, seed, sets_are_pre_split,
                 load_into_memory, set_name, num_tasks_per_epoch, overwrite_classes_in_each_task, same_class_interval):
        """
        A data provider class inheriting from Pytorch's Dataset class. It takes care of creating task sets for
        our few-shot learning model training and evaluation
        :param args: Arguments in the form of a Bunch object. Includes all hyperparameters necessary for the
        data-provider. For transparency and readability reasons to explicitly set as self.object_name all arguments
        required for the data provider, such that the reader knows exactly what is necessary for the data provider/
        """
        self.dataset_path = dataset_path
        self.dataset_name = dataset_name
        self.indexes_of_folders_indicating_class = indexes_of_folders_indicating_class

        self.labels_as_int = labels_as_int
        self.train_val_test_split = train_val_test_split

        self.num_samples_per_support_class = num_samples_per_support_class
        self.num_classes_per_set = num_classes_per_set
        self.num_samples_per_target_class = num_samples_per_target_class
        self.num_continual_subtasks_per_task = num_continual_subtasks_per_task
        self.overwrite_classes_in_each_task = overwrite_classes_in_each_task
        self.same_class_interval = same_class_interval

        self.dataset = load_dataset(dataset_path, dataset_name, labels_as_int, seed, sets_are_pre_split,
                                    load_into_memory,
                                    indexes_of_folders_indicating_class, train_val_test_split)[set_name]

        self.num_tasks_per_epoch = num_tasks_per_epoch

        self.dataset_size_dict = {key: len(self.dataset[key]) for key in list(self.dataset.keys())}

        self.index_to_label_name_dict_file = "{}/map_to_label_name_{}.json".format(dataset_path, dataset_name)
        self.label_name_to_map_dict_file = "{}/label_name_to_map_{}.json".format(dataset_path, dataset_name)

        self.label_set = get_label_set(index_to_label_name_dict_file=self.index_to_label_name_dict_file)
        self.data_length = np.sum([len(self.dataset[key]) for key in self.dataset])
        self.num_channels = num_channels
        self.load_into_memory = load_into_memory
        self.current_iter = 0

        if self.load_into_memory:
            print('load_into_memory flag is True. Loading the {} set into memory'.format(set_name))
            dataset_loaded = defaultdict(list)
            with tqdm.tqdm(total=len(self.dataset.items())) as pbar:
                for key, file_paths in self.dataset.items():
                    file_path_transforms_list = [(file_path, transforms) for file_path in file_paths]
                    with tqdm.tqdm(total=len(file_paths)) as pbar_process_images:
                        with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
                            for processed_image in executor.map(load_preprocess_image, file_path_transforms_list):
                                dataset_loaded[key].append(processed_image)
                                pbar_process_images.update(1)
                    pbar.update(1)
            self.dataset = dataset_loaded
        self.seed = seed
        self.transforms = transforms

        print("data", self.data_length)

    def get_set(self, seed, class_seed, num_channels):
        """
        Generates a task-set to be used for training or evaluation
        :param set_name: The name of the set to use, e.g. "train", "val" etc.
        :return: A task-set containing an image and label support set, and an image and label target set.
        """

        rng = np.random.RandomState(seed)
        class_rng = np.random.RandomState(class_seed)
        selected_classes = class_rng.choice(list(self.dataset_size_dict.keys()),
                                            size=self.num_classes_per_set, replace=False)
        class_rng.shuffle(selected_classes)
        # print(selected_classes)

        episode_labels = [i for i in range(self.num_classes_per_set)]

        class_to_episode_label = {selected_class: episode_label for (selected_class, episode_label) in
                                  zip(selected_classes, episode_labels)}

        set_paths = [self.dataset[class_idx][sample_idx] for
                     class_idx in selected_classes for sample_idx in
                     rng.choice(len(self.dataset[class_idx]),
                                size=self.num_samples_per_support_class + self.num_samples_per_target_class,
                                replace=False)]

        if not self.load_into_memory:
            x = [augment_image(load_image(image_path), transforms=self.transforms) for image_path in set_paths]
        else:
            x = [torch.tensor(image_path.copy()) for image_path in set_paths]

        y = np.array([(self.num_samples_per_support_class + self.num_samples_per_target_class) * [
            class_to_episode_label[class_idx]]
                      for class_idx in selected_classes])

        for idx, item in enumerate(x):
            if not item.shape[0] == num_channels:
                if item.shape[0] > num_channels:
                    x[idx] = x[idx][:num_channels]
                elif item.shape[0] == 1:
                    x[idx] = item.repeat([num_channels, 1, 1])

        x = torch.stack(x)

        y = y.reshape(1, self.num_classes_per_set,
                      self.num_samples_per_support_class + self.num_samples_per_target_class)

        x = x.view(1, self.num_classes_per_set,
                   self.num_samples_per_support_class + self.num_samples_per_target_class, x.shape[1], x.shape[2],
                   x.shape[3])

        x_support_set = x[:, :, :self.num_samples_per_support_class]
        y_support_set = y[:, :, :self.num_samples_per_support_class]

        x_target_set = x[:, :, self.num_samples_per_support_class:]
        y_target_set = y[:, :, self.num_samples_per_support_class:]

        return x_support_set, x_target_set, y_support_set, y_target_set, torch.Tensor(
            [int(item) for item in selected_classes])

    def set_current_iter_idx(self, idx):
        self.seed = self.seed + (idx * self.num_continual_subtasks_per_task)
        self.current_iter = idx * self.num_continual_subtasks_per_task

    def __len__(self):
        return self.num_tasks_per_epoch - self.current_iter

    def __getitem__(self, idx):
        # print(int(idx / self.same_class_interval))
        return self.get_set(class_seed=int(idx / self.same_class_interval), seed=self.seed + idx,
                            num_channels=self.num_channels)


def load_preprocess_image(file_path_transform):
    image_path, transform = file_path_transform

    loaded_image = load_image(image_path)

    preprocessed_image = augment_image(loaded_image, transforms=transform).numpy()

    return preprocessed_image

