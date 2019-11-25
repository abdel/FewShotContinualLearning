import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class Conv2dNormLeakyReLU(nn.Module):
    def __init__(self, input_shape, num_filters, kernel_size, dilation=1, stride=1, groups=1, padding=0, use_bias=False,
                 normalization=True, weight_attention=False):
        super(Conv2dNormLeakyReLU, self).__init__()
        self.input_shape = list(input_shape)
        self.num_filters = num_filters
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.use_bias = use_bias
        self.normalization = normalization
        self.dilation = dilation
        self.weight_attention = weight_attention
        self.groups = groups
        self.layer_dict = nn.ModuleDict()
        self.build_network()

    def build_network(self):
        x = torch.ones(self.input_shape)
        out = x

        self.layer_dict['conv'] = nn.Conv2d(in_channels=out.shape[1], out_channels=self.num_filters,
                                            kernel_size=self.kernel_size, stride=self.stride, padding=self.padding,
                                            dilation=self.dilation, groups=self.groups, bias=self.use_bias)

        out = self.layer_dict['conv'].forward(out)

        if self.normalization:
            self.layer_dict['norm_layer'] = nn.BatchNorm2d(num_features=out.shape[1])
            out = self.layer_dict['norm_layer'](out)

        self.layer_dict['relu'] = nn.LeakyReLU()
        out = self.layer_dict['relu'](out)
        print(out.shape)

    def forward(self, x):
        out = x

        out = self.layer_dict['conv'].forward(out)

        if self.normalization:
            out = self.layer_dict['norm_layer'](out)

        out = self.layer_dict['relu'](out)
        return out


class DenseNetActivationNormNetwork(nn.Module):
    def __init__(self, im_shape, num_filters, num_stages, num_blocks_per_stage, dropout_rate, average_pool_output,
                 reduction_rate, conv_type):
        """
        Builds a multilayer convolutional network. It also provides functionality for passing external parameters to be
        used at inference time. Enables inner loop optimization readily.
        :param im_shape: The input image batch shape.
        :param num_output_classes: The number of output classes of the network.
        :param args: A named tuple containing the system's hyperparameters.
        :param device: The device to run this on.
        :param meta_classifier: A flag indicating whether the system's meta-learning (inner-loop) functionalities should
        be enabled.
        """
        super(DenseNetActivationNormNetwork, self).__init__()
        self.input_shape = list(im_shape)
        self.num_filters = num_filters
        self.num_stages = num_stages
        self.dropout_rate = dropout_rate
        self.reduction_rate = reduction_rate
        self.average_pool_output = average_pool_output
        self.conv_type = conv_type
        # self.num_output_classes = num_output_classes
        self.num_blocks_per_stage = num_blocks_per_stage
        self.layer_dict = nn.ModuleDict()
        self.build_network()

    def build_network(self):
        """
        Builds the network before inference is required by creating some dummy inputs with the same input as the
        self.im_shape tuple. Then passes that through the network and dynamically computes input shapes and
        sets output shapes for each layer.
        """
        x = torch.zeros(self.input_shape)
        out = x

        self.layer_dict['stem_conv'] = self.conv_type(input_shape=out.shape, num_filters=64,
                                                      kernel_size=3, padding=1)

        out = self.layer_dict['stem_conv'](out)

        for i in range(self.num_stages):
            for j in range(self.num_blocks_per_stage):
                self.layer_dict['conv_bottleneck_{}_{}'.format(i, j)] = self.conv_type(input_shape=out.shape,
                                                                                       num_filters=self.num_filters,
                                                                                       kernel_size=1, padding=0)

                cur = self.layer_dict['conv_bottleneck_{}_{}'.format(i, j)](out)
                self.layer_dict['conv_{}_{}'.format(i, j)] = self.conv_type(input_shape=cur.shape,
                                                                            num_filters=self.num_filters,
                                                                            kernel_size=3, padding=1)

                cur = self.layer_dict['conv_{}_{}'.format(i, j)](cur)
                cur = F.dropout(cur, p=self.dropout_rate, training=True)
                out = torch.cat([out, cur], dim=1)

            out = F.avg_pool2d(out, 2)
            print(out.shape)
            self.layer_dict['transition_layer_{}'.format(i)] = self.conv_type(input_shape=out.shape,
                                                                              num_filters=int(out.shape[
                                                                                                  1] * self.reduction_rate),
                                                                              kernel_size=1, padding=0)

            out = self.layer_dict['transition_layer_{}'.format(i)](out)

        if self.average_pool_output:
            out = F.avg_pool2d(out, out.shape[2])
            out = out.view(out.shape[0], -1)
        else:
            out = F.adaptive_avg_pool2d(out, output_size=(5, 5))

        # self.layer_dict['adaptor_layer'] = Conv2dNormLeakyReLU(input_shape=out.shape,
        #                                                              num_filters=64,
        #                                                              kernel_size=1, padding=0)
        # out = self.layer_dict['adaptor_layer'].forward(out)

        print(out.shape)

    def forward(self, x, dropout_training):
        """
        Forward propages through the network. If any params are passed then they are used instead of stored params.
        :param x: Input image batch.
        :param num_step: The current inner loop step number
        :param params: If params are None then internal parameters are used. If params are a dictionary with keys the
         same as the layer names then they will be used instead.
        :param training: Whether this is training (True) or eval time.
        :param backup_running_statistics: Whether to backup the running statistics in their backup store. Which is
        then used to reset the stats back to a previous state (usually after an eval loop, when we want to throw away stored statistics)
        :return: Logits of shape b, num_output_classes.
        """
        out = x

        out = self.layer_dict['stem_conv'](out)
        for i in range(self.num_stages):
            for j in range(self.num_blocks_per_stage):
                cur = self.layer_dict['conv_bottleneck_{}_{}'.format(i, j)](out)
                cur = self.layer_dict['conv_{}_{}'.format(i, j)](cur)
                cur = F.dropout(cur, p=self.dropout_rate, training=dropout_training)
                out = torch.cat([out, cur], dim=1)

            out = F.avg_pool2d(out, 2)
            out = self.layer_dict['transition_layer_{}'.format(i)](out)

        if self.average_pool_output:
            out = F.avg_pool2d(out, out.shape[2])
            out = out.view(out.shape[0], -1)
        else:
            out = F.adaptive_avg_pool2d(out, output_size=(5, 5))

        # out = self.layer_dict['adaptor_layer'].forward(out)

        return out


class SqueezeExciteDenseNet(nn.Module):
    def __init__(self, im_shape, num_filters, num_stages, num_blocks_per_stage, dropout_rate, average_pool_output,
                 reduction_rate, output_spatial_dim, use_channel_wise_attention):
        """
        Builds a multilayer convolutional network. It also provides functionality for passing external parameters to be
        used at inference time. Enables inner loop optimization readily.
        :param im_shape: The input image batch shape.
        :param num_output_classes: The number of output classes of the network.
        :param args: A named tuple containing the system's hyperparameters.
        :param device: The device to run this on.
        :param meta_classifier: A flag indicating whether the system's meta-learning (inner-loop) functionalities should
        be enabled.
        """
        super(SqueezeExciteDenseNet, self).__init__()
        self.input_shape = list(im_shape)
        self.num_filters = num_filters
        self.num_stages = num_stages
        self.dropout_rate = dropout_rate
        self.reduction_rate = reduction_rate
        self.average_pool_output = average_pool_output
        # self.num_output_classes = num_output_classes
        self.num_blocks_per_stage = num_blocks_per_stage
        self.output_spatial_dim = output_spatial_dim
        self.conv_type = Conv2dNormLeakyReLU
        self.layer_dict = nn.ModuleDict()
        self.use_channel_wise_attention = use_channel_wise_attention
        self.build_network()

    def build_network(self):
        """
        Builds the network before inference is required by creating some dummy inputs with the same input as the
        self.im_shape tuple. Then passes that through the network and dynamically computes input shapes and
        sets output shapes for each layer.
        """
        x = torch.zeros(self.input_shape)
        out = x

        self.layer_dict['stem_conv'] = Conv2dNormLeakyReLU(input_shape=out.shape, num_filters=64,
                                                           kernel_size=3, padding=1, groups=1)

        out = self.layer_dict['stem_conv'](out)

        for i in range(self.num_stages):
            for j in range(self.num_blocks_per_stage):
                if self.use_channel_wise_attention:
                    attention_network_out = F.adaptive_avg_pool2d(out, 5).squeeze()

                    out_channels = attention_network_out.view(attention_network_out.shape[0], -1)

                    # self.layer_dict['channel_wise_attention_prediction_units_{}_{}'.format(j, i)] = BatchRelationalModule(
                    #     input_shape=attention_network_out.shape, use_coordinates=True)
                    # attention_network_out = self.layer_dict[
                    #     'channel_wise_attention_prediction_units_{}_{}'.format(j, i)].forward(attention_network_out)

                    self.layer_dict['channel_wise_attention_output_fcc_{}_{}'.format(j, i)] = nn.Linear(
                        in_features=out_channels.shape[1], out_features=out.shape[1], bias=True)
                    channel_wise_attention_regions = self.layer_dict['channel_wise_attention_output_fcc_{}_{}'.format(j, i)].forward(out_channels)

                    channel_wise_attention_regions = F.sigmoid(channel_wise_attention_regions)
                    out = out * channel_wise_attention_regions.unsqueeze(2).unsqueeze(2)

                self.layer_dict['conv_bottleneck_{}_{}'.format(i, j)] = self.conv_type(input_shape=out.shape,
                                                                                       num_filters=self.num_filters,
                                                                                       kernel_size=1, padding=0)

                cur = self.layer_dict['conv_bottleneck_{}_{}'.format(i, j)](out)
                self.layer_dict['conv_{}_{}'.format(i, j)] = self.conv_type(input_shape=cur.shape,
                                                                            num_filters=self.num_filters,
                                                                            kernel_size=3, padding=1, groups=1)

                cur = self.layer_dict['conv_{}_{}'.format(i, j)](cur)
                cur = F.dropout(cur, p=self.dropout_rate, training=True)
                out = torch.cat([out, cur], dim=1)

            out = F.avg_pool2d(out, 2)
            print(out.shape)
            self.layer_dict['transition_layer_{}'.format(i)] = Conv2dNormLeakyReLU(input_shape=out.shape,
                                                                                   num_filters=int(out.shape[
                                                                                                       1] * self.reduction_rate),
                                                                                   kernel_size=1, padding=0)

            out = self.layer_dict['transition_layer_{}'.format(i)](out)

        if self.average_pool_output:
            out = F.avg_pool2d(out, out.shape[2])
            out = out.view(out.shape[0], -1)
        else:
            out = F.adaptive_avg_pool2d(out, output_size=(self.output_spatial_dim, self.output_spatial_dim))

        print(out.shape)

    def forward(self, x, dropout_training):
        """
        Forward propages through the network. If any params are passed then they are used instead of stored params.
        :param x: Input image batch.
        :param num_step: The current inner loop step number
        :param params: If params are None then internal parameters are used. If params are a dictionary with keys the
         same as the layer names then they will be used instead.
        :param training: Whether this is training (True) or eval time.
        :param backup_running_statistics: Whether to backup the running statistics in their backup store. Which is
        then used to reset the stats back to a previous state (usually after an eval loop, when we want to throw away stored statistics)
        :return: Logits of shape b, num_output_classes.
        """
        out = x

        out = self.layer_dict['stem_conv'](out)
        for i in range(self.num_stages):
            for j in range(self.num_blocks_per_stage):
                # out_channels = F.avg_pool2d(out, out.shape[-1]).squeeze()
                if self.use_channel_wise_attention:

                    out_channels = F.adaptive_avg_pool2d(out, 5).squeeze()

                    out_channels = out_channels.view(out_channels.shape[0], -1)

                    # out_channels = self.layer_dict[
                    #     'channel_wise_attention_prediction_units_{}_{}'.format(j, i)].forward(
                    #     out_channels)

                    channel_wise_attention_regions = self.layer_dict[
                        'channel_wise_attention_output_fcc_{}_{}'.format(j, i)].forward(out_channels)

                    channel_wise_attention_regions = F.sigmoid(channel_wise_attention_regions)
                    out = out * channel_wise_attention_regions.unsqueeze(2).unsqueeze(2)


                cur = self.layer_dict['conv_bottleneck_{}_{}'.format(i, j)](out)
                cur = self.layer_dict['conv_{}_{}'.format(i, j)](cur)
                cur = F.dropout(cur, p=self.dropout_rate, training=dropout_training)
                out = torch.cat([out, cur], dim=1)

            out = F.avg_pool2d(out, 2)
            out = self.layer_dict['transition_layer_{}'.format(i)](out)

        if self.average_pool_output:
            out = F.avg_pool2d(out, out.shape[2])
            out = out.view(out.shape[0], -1)
        else:
            out = F.adaptive_avg_pool2d(out, output_size=(self.output_spatial_dim, self.output_spatial_dim))

        return out


class Conv1dNormLeakyReLU(nn.Module):
    def __init__(self, input_shape, num_filters, kernel_size, dilation=1, stride=1, groups=1, padding=0, use_bias=False,
                 normalization=True):
        super(Conv1dNormLeakyReLU, self).__init__()
        self.input_shape = list(input_shape)
        self.num_filters = num_filters
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.use_bias = use_bias
        self.normalization = normalization
        self.dilation = dilation
        self.groups = groups
        self.layer_dict = nn.ModuleDict()
        self.build_network()

    def build_network(self):
        x = torch.ones(self.input_shape)
        out = x
        self.layer_dict['conv'] = nn.Conv1d(in_channels=out.shape[1], out_channels=self.num_filters,
                                            kernel_size=self.kernel_size, stride=self.stride, padding=self.padding,
                                            dilation=self.dilation, groups=self.groups, bias=self.use_bias)
        out = self.layer_dict['conv'](out)
        if self.normalization:
            self.layer_dict['norm_layer'] = nn.BatchNorm1d(num_features=out.shape[1])
            out = self.layer_dict['norm_layer'](out)

        self.layer_dict['relu'] = nn.LeakyReLU()
        out = self.layer_dict['relu'](out)
        print(out.shape)

    def forward(self, x):
        out = x

        out = self.layer_dict['conv'](out)

        if self.normalization:
            out = self.layer_dict['norm_layer'](out)

        out = self.layer_dict['relu'](out)
        return out


class DilatedDenseNetActivationNormNetwork(nn.Module):
    def __init__(self, im_shape, num_filters, num_stages, num_blocks_per_stage, per_param_biases=False):
        """
        Builds a multilayer convolutional network. It also provides functionality for passing external parameters to be
        used at inference time. Enables inner loop optimization readily.
        :param im_shape: The input image batch shape.
        :param num_output_classes: The number of output classes of the network.
        :param args: A named tuple containing the system's hyperparameters.
        :param device: The device to run this on.
        :param meta_classifier: A flag indicating whether the system's meta-learning (inner-loop) functionalities should
        be enabled.
        """
        super(DilatedDenseNetActivationNormNetwork, self).__init__()
        self.input_shape = list(im_shape)
        self.num_filters = num_filters
        self.num_stages = num_stages
        # self.num_output_classes = num_output_classes
        self.num_blocks_per_stage = num_blocks_per_stage
        self.use_per_param_biases = per_param_biases
        self.layer_dict = nn.ModuleDict()
        self.build_network()

    def build_network(self):
        """
        Builds the network before inference is required by creating some dummy inputs with the same input as the
        self.im_shape tuple. Then passes that through the network and dynamically computes input shapes and
        sets output shapes for each layer.
        """
        x = torch.zeros(self.input_shape)
        out = x

        self.layer_dict['stem_conv'] = Conv2dNormLeakyReLU(input_shape=out.shape, num_filters=self.num_filters,
                                                           kernel_size=3, padding=1)

        out = self.layer_dict['stem_conv'](out)

        for i in range(2):
            for j in range(8):
                dilation = 2 ** j
                self.layer_dict['conv_{}_{}'.format(i, j)] = Conv2dNormLeakyReLU(input_shape=out.shape,
                                                                                 num_filters=8,
                                                                                 kernel_size=3, padding=dilation,
                                                                                 dilation=dilation)

                cur = self.layer_dict['conv_{}_{}'.format(i, j)](out)
                out = torch.cat([out, cur], dim=1)

        self.layer_dict['out_conv'] = nn.Conv2d(in_channels=out.shape[1], out_channels=self.input_shape[1], bias=True,
                                                kernel_size=3, padding=1)

        out = self.layer_dict['out_conv'](out)

        if self.use_per_param_biases:
            biases = torch.zeros(out.shape)

            self.bias_params = nn.Parameter(biases, requires_grad=True)

            out = out + self.bias_params

    def forward(self, x):
        """
        Forward propages through the network. If any params are passed then they are used instead of stored params.
        :param x: Input image batch.
        :param num_step: The current inner loop step number
        :param params: If params are None then internal parameters are used. If params are a dictionary with keys the
         same as the layer names then they will be used instead.
        :param training: Whether this is training (True) or eval time.
        :param backup_running_statistics: Whether to backup the running statistics in their backup store. Which is
        then used to reset the stats back to a previous state (usually after an eval loop, when we want to throw away stored statistics)
        :return: Logits of shape b, num_output_classes.
        """
        out = x
        out = self.layer_dict['stem_conv'](out)

        for i in range(2):
            for j in range(8):
                dilation = 2 ** j
                cur = self.layer_dict['conv_{}_{}'.format(i, j)](out)
                out = torch.cat([out, cur], dim=1)

        out = self.layer_dict['out_conv'](out)

        if self.use_per_param_biases:
            out = out + self.bias_params

        return out


class Dilated1dDenseNetActivationNormNetwork(nn.Module):
    def __init__(self, im_shape, num_filters, num_stages, num_blocks_per_stage):
        """
        Builds a multilayer convolutional network. It also provides functionality for passing external parameters to be
        used at inference time. Enables inner loop optimization readily.
        :param im_shape: The input image batch shape.
        :param num_output_classes: The number of output classes of the network.
        :param args: A named tuple containing the system's hyperparameters.
        :param device: The device to run this on.
        :param meta_classifier: A flag indicating whether the system's meta-learning (inner-loop) functionalities should
        be enabled.
        """
        super(Dilated1dDenseNetActivationNormNetwork, self).__init__()
        self.input_shape = list(im_shape)
        self.num_filters = num_filters
        self.num_stages = num_stages
        # self.num_output_classes = num_output_classes
        self.num_blocks_per_stage = num_blocks_per_stage
        self.layer_dict = nn.ModuleDict()
        self.build_network()

    def build_network(self):
        """
        Builds the network before inference is required by creating some dummy inputs with the same input as the
        self.im_shape tuple. Then passes that through the network and dynamically computes input shapes and
        sets output shapes for each layer.
        """
        x = torch.zeros(self.input_shape)
        out = x

        self.layer_dict['stem_conv'] = Conv1dNormLeakyReLU(input_shape=out.shape, num_filters=self.num_filters,
                                                           kernel_size=3, padding=1)

        out = self.layer_dict['stem_conv'](out)

        for i in range(1):
            for j in range(11):
                dilation = 2 ** j
                self.layer_dict['conv_{}_{}'.format(i, j)] = Conv1dNormLeakyReLU(input_shape=out.shape,
                                                                                 num_filters=self.num_filters,
                                                                                 kernel_size=3, padding=dilation,
                                                                                 dilation=dilation)

                cur = self.layer_dict['conv_{}_{}'.format(i, j)](out)
                out = torch.cat([out, cur], dim=1)

        self.layer_dict['out_conv'] = nn.Conv1d(in_channels=out.shape[1], out_channels=self.input_shape[1], bias=True,
                                                kernel_size=3, padding=1)

        out = self.layer_dict['out_conv'](out)

    def forward(self, x):
        """
        Forward propages through the network. If any params are passed then they are used instead of stored params.
        :param x: Input image batch.
        :param num_step: The current inner loop step number
        :param params: If params are None then internal parameters are used. If params are a dictionary with keys the
         same as the layer names then they will be used instead.
        :param training: Whether this is training (True) or eval time.
        :param backup_running_statistics: Whether to backup the running statistics in their backup store. Which is
        then used to reset the stats back to a previous state (usually after an eval loop, when we want to throw away stored statistics)
        :return: Logits of shape b, num_output_classes.
        """
        out = x
        out = self.layer_dict['stem_conv'](out)

        for i in range(1):
            for j in range(11):
                dilation = 2 ** j
                cur = self.layer_dict['conv_{}_{}'.format(i, j)](out)
                out = torch.cat([out, cur], dim=1)

        out = self.layer_dict['out_conv'](out)
        return out


class CriticNetwork(nn.Module):
    def __init__(self, task_embedding_shape, logit_shape, support_set_feature_shape, target_set_feature_shape,
                 support_set_classifier_pre_last_features,
                 target_set_classifier_pre_last_features,
                 support_set_label_shape,
                 num_classes_per_set, num_samples_per_class,
                 num_target_samples, args):
        """
        Builds a multilayer convolutional network. It also provides functionality for passing external parameters to be
        used at inference time. Enables inner loop optimization readily.
        :param im_shape: The input image batch shape.
        :param num_output_classes: The number of output classes of the network.
        :param args: A named tuple containing the system's hyperparameters.
        :param device: The device to run this on.
        :param meta_classifier: A flag indicating whether the system's meta-learning (inner-loop) functionalities should
        be enabled.
        """
        super(CriticNetwork, self).__init__()

        self.layer_dict = nn.ModuleDict()
        self.num_target_samples = num_target_samples
        self.num_samples_per_class = num_samples_per_class
        self.num_classes_per_set = num_classes_per_set
        self.logit_shape = logit_shape
        self.task_embedding_shape = task_embedding_shape
        self.conditional_information = args.conditional_information
        self.support_set_feature_shape = support_set_feature_shape
        self.target_set_feature_shape = target_set_feature_shape
        self.support_set_classifier_pre_last_features = support_set_classifier_pre_last_features
        self.target_set_classifier_pre_last_features = target_set_classifier_pre_last_features
        self.support_set_label_shape = support_set_label_shape
        self.build_network()

    def build_network(self):
        """
        Builds the network before inference is required by creating some dummy inputs with the same input as the
        self.im_shape tuple. Then passes that through the network and dynamically computes input shapes and
        sets output shapes for each layer.
        """
        processed_feature_list = []

        if 'preds' in self.conditional_information:
            logits = torch.ones(self.logit_shape)
            logits_abs_diff_targets = torch.abs(logits)

            logits_square_diff_targets = logits ** 2

            sign_logits = torch.sign(logits)

            logit_targets_features = torch.cat(
                [logits, logits_abs_diff_targets, logits_square_diff_targets, sign_logits], dim=1)

            logit_targets_features = logit_targets_features.view(logit_targets_features.shape[0], 1,
                                                                 logit_targets_features.shape[1])
            processed_feature_list.append(logit_targets_features)

        if 'task_embedding' in self.conditional_information:
            task_embedding = torch.zeros(self.task_embedding_shape)
            task_embed_batched = task_embedding.view(1, 1, -1)
            if 'preds' in self.conditional_information:
                task_embed_batched = task_embed_batched.repeat(processed_feature_list[0].shape[0], 1, 1)

            processed_feature_list.append(task_embed_batched)

        # print(param_features_batched.shape, logit_targets_features.shape)
        for item in processed_feature_list:
            print('this one', item.shape)
        mixed_features = torch.cat(processed_feature_list, dim=2)

        feature_sets = [mixed_features]
        # print(feature_sets.shape)
        for i in range(5):
            dilation = 2 ** i
            cur = torch.cat(feature_sets, dim=1)
            self.layer_dict['dilated_conv1d_{}'.format(i)] = nn.Conv1d(in_channels=cur.shape[1],
                                                                       out_channels=8, kernel_size=3,
                                                                       dilation=dilation, padding=dilation)
            cur = self.layer_dict['dilated_conv1d_{}'.format(i)](cur)
            self.layer_dict['norm_layer_{}'.format(i)] = nn.BatchNorm1d(num_features=cur.shape[1])
            cur = self.layer_dict['norm_layer_{}'.format(i)](cur)
            cur = F.relu(cur)
            feature_sets.append(cur)

        out = torch.cat(feature_sets, dim=1)

        out = out.view(out.shape[0], -1)

        self.layer_dict['linear_0'] = nn.Linear(in_features=out.shape[1],
                                                out_features=16, bias=False)

        out = self.layer_dict['linear_0'](out)
        out = F.relu(out)

        self.layer_dict['linear_1'] = nn.Linear(in_features=out.shape[1],
                                                out_features=16, bias=False)

        out = self.layer_dict['linear_1'](out)
        out = F.relu(out)

        self.layer_dict['linear_preds'] = nn.Linear(in_features=out.shape[1],
                                                    out_features=1, bias=False)

        out = self.layer_dict['linear_preds'](out)

        out = out.sum()
        print("VGGNetwork build", out.shape)

    def forward(self, support_set_features, target_set_features, logits, support_set_classifier_pre_last_layer,
                target_set_classifier_pre_last_layer, support_set_labels, task_embedding, return_sum=True):
        """
        Forward propages through the network. If any params are passed then they are used instead of stored params.
        :param x: Input image batch.
        :param num_step: The current inner loop step number
        :param params: If params are None then internal parameters are used. If params are a dictionary with keys the
         same as the layer names then they will be used instead.
        :param training: Whether this is training (True) or eval time.
        :param backup_running_statistics: Whether to backup the running statistics in their backup store. Which is
        then used to reset the stats back to a previous state (usually after an eval loop, when we want to throw away stored statistics)
        :return: Logits of shape b, num_output_classes.
        """

        # print(logits.shape, task_embedding.shape, support_set_features.shape, target_set_features.shape,
        #       support_set_classifier_pre_last_layer.shape, target_set_classifier_pre_last_layer.shape,
        #       support_set_labels.shape)

        processed_feature_list = []

        if 'preds' in self.conditional_information:
            logits_abs_diff_targets = torch.abs(logits)

            logits_square_diff_targets = logits ** 2

            sign_logits = torch.sign(logits)

            logit_targets_features = torch.cat(
                [logits, logits_abs_diff_targets, logits_square_diff_targets, sign_logits], dim=1)

            logit_targets_features = logit_targets_features.view(logit_targets_features.shape[0], 1,
                                                                 logit_targets_features.shape[1])
            processed_feature_list.append(logit_targets_features)

        if 'task_embedding' in self.conditional_information:
            task_embed_batched = task_embedding.view(1, 1, -1)
            if 'preds' in self.conditional_information:
                task_embed_batched = task_embed_batched.repeat(processed_feature_list[0].shape[0], 1, 1)

            processed_feature_list.append(task_embed_batched)

        mixed_features = torch.cat(processed_feature_list, dim=2)

        feature_sets = [mixed_features]
        for i in range(5):
            dilation = 2 ** i
            cur = torch.cat(feature_sets, dim=1)

            cur = self.layer_dict['dilated_conv1d_{}'.format(i)](cur)

            cur = self.layer_dict['norm_layer_{}'.format(i)](cur)
            cur = F.relu(cur)
            feature_sets.append(cur)

        out = torch.cat(feature_sets, dim=1)

        out = out.view(out.shape[0], -1)

        out = self.layer_dict['linear_0'](out)
        out = F.relu(out)

        out = self.layer_dict['linear_1'](out)
        out = F.relu(out)

        out = self.layer_dict['linear_preds'](out)

        if return_sum:
            out = out.sum()

        return out


class TaskRelationalEmbedding(nn.Module):
    def __init__(self, input_shape, num_samples_per_class, num_classes_per_set):
        super(TaskRelationalEmbedding, self).__init__()

        self.input_shape = input_shape
        self.block_dict = nn.ModuleDict()
        self.num_samples_per_class = num_samples_per_class
        self.num_classes_per_set = num_classes_per_set
        self.first_time = True
        self.build_block()

    def build_block(self):
        out_img = torch.zeros(self.input_shape)
        """g"""
        b, f = out_img.shape
        print(out_img.shape)
        out_img = out_img.view(b, f)
        print(out_img.shape)
        # x_flat = (64 x 25 x 24)
        self.coord_tensor = []
        for i in range(b):
            self.coord_tensor.append(torch.Tensor(np.array([i])))

        self.coord_tensor = torch.stack(self.coord_tensor, dim=0)
        out_img = torch.cat([out_img, self.coord_tensor], dim=1)

        x_i = torch.unsqueeze(out_img, 0)  # (1xh*wxc)
        x_i = x_i.repeat(b, 1, 1)  # (h*wxh*wxc)
        x_j = torch.unsqueeze(out_img, 1)  # (h*wx1xc)
        x_j = x_j.repeat(1, b, 1)  # (h*wxh*wxc)

        # concatenate all together
        out = torch.cat([x_i, x_j], 2)  # (h*wxh*wx2*c)
        prev_shape = out.shape
        out = out.view(out.shape[0] * out.shape[1], out.shape[-1])
        for idx_layer in range(3):
            self.block_dict['g_fcc_{}'.format(idx_layer)] = nn.Linear(out.shape[1], out_features=32)
            out = F.relu(self.block_dict['g_fcc_{}'.format(idx_layer)].forward(out))

        # reshape again and sum

        out = out.view(prev_shape[0], prev_shape[1], out.shape[-1])
        out = out.sum(1)
        out = out.view(self.num_classes_per_set, self.num_samples_per_class, -1)
        out = out.mean(1).view(1, -1)

        print('Task Relational Network Block built with output volume shape', out.shape)

    def forward(self, x_img):

        out_img = x_img
        # print("input", out_img.shape)
        """g"""
        b, f = out_img.shape
        out_img = out_img.view(b, f)

        out_img = torch.cat([out_img, self.coord_tensor.to(x_img.device)], dim=1)
        # x_flat = (64 x 25 x 24)
        # print('out_img', out_img.shape)
        x_i = torch.unsqueeze(out_img, 0)  # (1xh*wxc)
        x_i = x_i.repeat(b, 1, 1)  # (h*wxh*wxc)
        x_j = torch.unsqueeze(out_img, 1)  # (h*wx1xc)
        x_j = x_j.repeat(1, b, 1)  # (h*wxh*wxc)

        # concatenate all together
        out = torch.cat([x_i, x_j], 2)  # (h*wxh*wx2*c)

        prev_shape = out.shape
        out = out.view(out.shape[0] * out.shape[1], out.shape[-1])
        for idx_layer in range(3):
            out = F.relu(self.block_dict['g_fcc_{}'.format(idx_layer)].forward(out))

        # reshape again and sum
        # print(out.shape)
        out = out.view(prev_shape[0], prev_shape[1], out.shape[-1])
        out = out.sum(1)
        out = out.view(self.num_classes_per_set, self.num_samples_per_class, -1)
        out = out.mean(1).view(1, -1)

        # """f"""
        # out = self.post_processing_layer.forward(out)
        # out = F.relu(out)
        # out = self.output_layer.forward(out)
        # # print('Block built with output volume shape', out.shape)
        return out


class RelationalModule(nn.Module):
    def __init__(self, input_shape):
        super(RelationalModule, self).__init__()

        self.input_shape = input_shape
        self.block_dict = nn.ModuleDict()
        self.first_time = True
        self.build_block()

    def build_block(self):
        out_img = torch.zeros(self.input_shape)
        """g"""
        c, h, w = out_img.shape
        print(out_img.shape)
        out_img = out_img.view(c, h * w)
        out_img = out_img.permute([1, 0])  # h*w, c
        print(out_img.shape)
        # x_flat = (64 x 25 x 24)
        self.coord_tensor = []
        for i in range(h * w):
            self.coord_tensor.append(torch.Tensor(np.array([i])))

        self.coord_tensor = torch.stack(self.coord_tensor, dim=0)
        out_img = torch.cat([out_img, self.coord_tensor], dim=1)

        x_i = torch.unsqueeze(out_img, 0)  # (1xh*wxc)
        x_i = x_i.repeat(h * w, 1, 1)  # (h*wxh*wxc)
        x_j = torch.unsqueeze(out_img, 1)  # (h*wx1xc)
        x_j = x_j.repeat(1, h * w, 1)  # (h*wxh*wxc)

        # concatenate all together
        out = torch.cat([x_i, x_j], 2)  # (h*wxh*wx2*c)

        out = out.view(out.shape[0] * out.shape[1], out.shape[2])
        for idx_layer in range(3):
            self.block_dict['g_fcc_{}'.format(idx_layer)] = nn.Linear(out.shape[1], out_features=32)
            out = F.leaky_relu(self.block_dict['g_fcc_{}'.format(idx_layer)].forward(out))

        # reshape again and sum
        print(out.shape)
        out = out.sum(0).view(1, -1)

        """f"""
        self.post_processing_layer = nn.Linear(in_features=out.shape[1], out_features=32)
        out = self.post_processing_layer.forward(out)
        out = F.relu(out)
        self.output_layer = nn.Linear(in_features=out.shape[1], out_features=32)
        out = self.output_layer.forward(out)
        print('Block built with output volume shape', out.shape)

    def forward(self, x_img):

        out_img = x_img
        # print("input", out_img.shape)
        """g"""
        c, h, w = out_img.shape
        out_img = out_img.view(c, h * w)
        out_img = out_img.permute([1, 0])  # h*w, c
        out_img = torch.cat([out_img, self.coord_tensor.to(x_img.device)], dim=1)
        # x_flat = (64 x 25 x 24)
        # print('out_img', out_img.shape)
        x_i = torch.unsqueeze(out_img, 0)  # (1xh*wxc)
        x_i = x_i.repeat(h * w, 1, 1)  # (h*wxh*wxc)
        x_j = torch.unsqueeze(out_img, 1)  # (h*wx1xc)
        x_j = x_j.repeat(1, h * w, 1)  # (h*wxh*wxc)

        # concatenate all together
        out = torch.cat([x_i, x_j], 2)  # (h*wxh*wx2*c)
        out = out.view(out.shape[0] * out.shape[1], out.shape[2])
        for idx_layer in range(2):
            out = F.leaky_relu(self.block_dict['g_fcc_{}'.format(idx_layer)].forward(out))

        # reshape again and sum
        # print(out.shape)
        out = out.sum(0).view(1, -1)

        """f"""
        out = self.post_processing_layer.forward(out)
        out = F.relu(out)
        out = self.output_layer.forward(out)
        # print('Block built with output volume shape', out.shape)
        return out


class BatchRelationalModule(nn.Module):
    def __init__(self, input_shape, use_coordinates=True):
        super(BatchRelationalModule, self).__init__()

        self.input_shape = input_shape
        self.block_dict = nn.ModuleDict()
        self.first_time = True
        self.use_coordinates = use_coordinates
        self.build_block()

    def build_block(self):
        out_img = torch.zeros(self.input_shape)
        """g"""
        if len(out_img.shape) > 3:
            b, c, h, w = out_img.shape
            print(out_img.shape)
            out_img = out_img.view(b, c, h * w)

        out_img = out_img.permute([0, 2, 1])  # h*w, c
        b, length, c = out_img.shape
        print(out_img.shape)
        # x_flat = (64 x 25 x 24)
        if self.use_coordinates:
            self.coord_tensor = []
            for i in range(length):
                self.coord_tensor.append(torch.Tensor(np.array([i])))

            self.coord_tensor = torch.stack(self.coord_tensor, dim=0).unsqueeze(0)

            if self.coord_tensor.shape[0] != out_img.shape[0]:
                self.coord_tensor = self.coord_tensor[0].unsqueeze(0).repeat([out_img.shape[0], 1, 1])

            out_img = torch.cat([out_img, self.coord_tensor], dim=2)

        x_i = torch.unsqueeze(out_img, 1)  # (1xh*wxc)
        x_i = x_i.repeat(1, length, 1, 1)  # (h*wxh*wxc)
        x_j = torch.unsqueeze(out_img, 2)  # (h*wx1xc)
        x_j = x_j.repeat(1, 1, length, 1)  # (h*wxh*wxc)

        # concatenate all together
        per_location_feature = torch.cat([x_i, x_j], 3)  # (h*wxh*wx2*c)

        out = per_location_feature.view(
            per_location_feature.shape[0] * per_location_feature.shape[1] * per_location_feature.shape[2],
            per_location_feature.shape[3])
        print(out.shape)
        for idx_layer in range(2):
            self.block_dict['g_fcc_{}'.format(idx_layer)] = nn.Linear(out.shape[1], out_features=64, bias=True)
            out = self.block_dict['g_fcc_{}'.format(idx_layer)].forward(out)
            self.block_dict['LeakyReLU_{}'.format(idx_layer)] = nn.LeakyReLU()
            out = self.block_dict['LeakyReLU_{}'.format(idx_layer)].forward(out)

        # reshape again and sum
        print(out.shape)
        out = out.view(per_location_feature.shape[0], per_location_feature.shape[1], per_location_feature.shape[2], -1)
        out = out.sum(1).sum(1)
        print('here', out.shape)
        """f"""
        self.post_processing_layer = nn.Linear(in_features=out.shape[1], out_features=64)
        out = self.post_processing_layer.forward(out)
        self.block_dict['LeakyReLU_post_processing'] = nn.LeakyReLU()
        out = self.block_dict['LeakyReLU_post_processing'].forward(out)
        self.output_layer = nn.Linear(in_features=out.shape[1], out_features=64)
        out = self.output_layer.forward(out)
        self.block_dict['LeakyReLU_output'] = nn.LeakyReLU()
        out = self.block_dict['LeakyReLU_output'].forward(out)
        print('Block built with output volume shape', out.shape)

    def forward(self, x_img):

        out_img = x_img
        # print("input", out_img.shape)
        """g"""
        if len(out_img.shape) > 3:
            b, c, h, w = out_img.shape
            out_img = out_img.view(b, c, h * w)

        out_img = out_img.permute([0, 2, 1])  # h*w, c
        b, length, c = out_img.shape

        if self.use_coordinates:
            if self.coord_tensor.shape[0] != out_img.shape[0]:
                self.coord_tensor = self.coord_tensor[0].unsqueeze(0).repeat([out_img.shape[0], 1, 1])

            out_img = torch.cat([out_img, self.coord_tensor.to(x_img.device)], dim=2)
        # x_flat = (64 x 25 x 24)
        # print('out_img', out_img.shape)
        x_i = torch.unsqueeze(out_img, 1)  # (1xh*wxc)
        x_i = x_i.repeat(1, length, 1, 1)  # (h*wxh*wxc)
        x_j = torch.unsqueeze(out_img, 2)  # (h*wx1xc)
        x_j = x_j.repeat(1, 1, length, 1)  # (h*wxh*wxc)

        # concatenate all together
        per_location_feature = torch.cat([x_i, x_j], 3)  # (h*wxh*wx2*c)
        out = per_location_feature.view(
            per_location_feature.shape[0] * per_location_feature.shape[1] * per_location_feature.shape[2],
            per_location_feature.shape[3])

        for idx_layer in range(2):
            out = self.block_dict['g_fcc_{}'.format(idx_layer)].forward(out)
            out = self.block_dict['LeakyReLU_{}'.format(idx_layer)].forward(out)

        # reshape again and sum
        # print(out.shape)
        out = out.view(per_location_feature.shape[0], per_location_feature.shape[1], per_location_feature.shape[2], -1)
        out = out.sum(1).sum(1)

        """f"""
        out = self.post_processing_layer.forward(out)
        out = self.block_dict['LeakyReLU_post_processing'].forward(out)
        out = self.output_layer.forward(out)
        out = self.block_dict['LeakyReLU_output'].forward(out)
        # print('Block built with output volume shape', out.shape)
        return out


class DenseEmbeddingSmallNetwork(nn.Module):
    def __init__(self, im_shape, num_filters, num_blocks_per_stage, num_stages, dropout_rate,
                 output_spatial_dimensionality, average_pool_outputs=True, use_vgg_features=False):
        super(DenseEmbeddingSmallNetwork, self).__init__()
        b, c, self.h, self.w = im_shape
        self.total_layers = 0
        self.input_shape = list(im_shape)
        self.num_filters = num_filters
        self.num_blocks_per_stage = num_blocks_per_stage
        self.num_stages = num_stages
        self.average_pool_outputs = average_pool_outputs
        self.use_vgg_features = use_vgg_features
        self.output_spatial_dimensionality = output_spatial_dimensionality
        self.dropout_rate = dropout_rate
        self.layer_dict = nn.ModuleDict()
        self.build_block()

    def build_block(self):
        x = torch.ones(self.input_shape)
        out = x
        self.layer_dict['dense_net_features'] = DenseNetActivationNormNetwork(im_shape=x.shape,
                                                                              num_filters=self.num_filters,
                                                                              num_stages=self.num_stages,
                                                                              num_blocks_per_stage=self.num_blocks_per_stage,
                                                                              dropout_rate=self.dropout_rate,
                                                                              reduction_rate=1.0,
                                                                              average_pool_output=self.average_pool_outputs)
        out = self.layer_dict['dense_net_features'].forward(out, dropout_training=False)

        print("DenseEmbeddingSmallNetwork output shape", out.shape)
        return out

    def forward(self, x, dropout_training):
        out = x
        # print("inputs", x.shape)
        out = self.layer_dict['dense_net_features'].forward(out, dropout_training=dropout_training)
        # out = out.view(out.shape[0], out.shape[1], 1, 1)
        # b, c, h, w = out.shape
        return out

    def reinitialize(self):
        for name, module in self.named_modules():
            if type(module) == nn.Conv2d:
                module.reset_parameters()


class SqueezeExciteDenseNetEmbeddingSmallNetwork(nn.Module):
    def __init__(self, im_shape, num_filters, num_blocks_per_stage, num_stages, dropout_rate,
                 output_spatial_dimensionality, use_channel_wise_attention, average_pool_outputs=True, use_vgg_features=False,
                 conv_type=Conv2dNormLeakyReLU):
        super(SqueezeExciteDenseNetEmbeddingSmallNetwork, self).__init__()
        b, c, self.h, self.w = im_shape
        self.total_layers = 0
        self.input_shape = list(im_shape)
        self.num_filters = num_filters
        self.num_blocks_per_stage = num_blocks_per_stage
        self.num_stages = num_stages
        self.average_pool_outputs = average_pool_outputs
        self.use_vgg_features = use_vgg_features
        self.output_spatial_dimensionality = output_spatial_dimensionality
        self.use_channel_wise_attention = use_channel_wise_attention
        self.dropout_rate = dropout_rate
        self.conv_type = conv_type
        self.layer_dict = nn.ModuleDict()
        self.build_block()

    def build_block(self):
        x = torch.ones(self.input_shape)
        out = x
        self.layer_dict['dense_net_features'] = SqueezeExciteDenseNet(im_shape=x.shape,
                                                                      num_filters=self.num_filters,
                                                                      num_stages=self.num_stages,
                                                                      num_blocks_per_stage=self.num_blocks_per_stage,
                                                                      dropout_rate=self.dropout_rate,
                                                                      reduction_rate=1.0,
                                                                      average_pool_output=self.average_pool_outputs,
                                                                      output_spatial_dim=self.output_spatial_dimensionality, use_channel_wise_attention=self.use_channel_wise_attention)
        out = self.layer_dict['dense_net_features'].forward(out, dropout_training=False)

        print("DenseEmbeddingSmallNetwork output shape", out.shape)
        return out

    def forward(self, x, dropout_training):
        out = x
        # print("inputs", x.shape)
        out = self.layer_dict['dense_net_features'].forward(out, dropout_training=dropout_training)
        # out = out.view(out.shape[0], out.shape[1], 1, 1)
        # b, c, h, w = out.shape
        return out

    def reinitialize(self):
        for name, module in self.named_modules():
            if type(module) == nn.Conv2d:
                module.reset_parameters()


class TaskRelationalNetwork(nn.Module):
    def __init__(self, im_shape):
        super(TaskRelationalNetwork, self).__init__()
        self.total_layers = 0
        self.input_shape = list(im_shape)
        self.layer_dict = nn.ModuleDict()
        self.build_block()

    def build_block(self):
        x = torch.ones(self.input_shape)
        out = x

        out = out.unbind(dim=0)
        out = torch.stack(out, dim=1)
        out = out.view(out.shape[0], -1, out.shape[-1])
        self.layer_dict['relational_net'] = RelationalModule(input_shape=out.shape)
        out = self.layer_dict['relational_net'](out)

        print(out.shape)

    def forward(self, x):
        out = x
        out = out.unbind(dim=0)
        out = torch.stack(out, dim=1)
        out = out.view(out.shape[0], -1, out.shape[-1])
        out = self.layer_dict['relational_net'](out)
        return out

    def reinitialize(self):
        for name, module in self.named_modules():
            if type(module) == nn.Conv2d:
                module.reset_parameters()


class SupportSetLossNetwork(nn.Module):
    def __init__(self, task_embedding_shape, support_set_feature_shape,
                 num_classes_per_set, num_samples_per_class, args):
        """
        Builds a multilayer convolutional network. It also provides functionality for passing external parameters to be
        used at inference time. Enables inner loop optimization readily.
        :param im_shape: The input image batch shape.
        :param num_output_classes: The number of output classes of the network.
        :param args: A named tuple containing the system's hyperparameters.
        :param device: The device to run this on.
        :param meta_classifier: A flag indicating whether the system's meta-learning (inner-loop) functionalities should
        be enabled.
        """
        super(SupportSetLossNetwork, self).__init__()

        self.layer_dict = nn.ModuleDict()
        self.num_samples_per_class = num_samples_per_class
        self.num_classes_per_set = num_classes_per_set
        self.task_embedding_shape = task_embedding_shape
        self.conditional_information = args.conditional_information
        self.support_set_feature_shape = support_set_feature_shape
        self.build_network()

    def build_network(self):
        """
        Builds the network before inference is required by creating some dummy inputs with the same input as the
        self.im_shape tuple. Then passes that through the network and dynamically computes input shapes and
        sets output shapes for each layer.
        """
        processed_feature_list = []

        # if 'preds' in self.conditional_information:
        logits = torch.ones((self.num_samples_per_class * self.num_classes_per_set, self.num_classes_per_set))
        labels = torch.ones((self.num_samples_per_class * self.num_classes_per_set, self.num_classes_per_set))

        logits_abs_diff_targets = torch.abs(logits - labels)

        logits_square_diff_targets = (logits - labels) ** 2

        sign_logits = torch.sign(logits - labels)

        cross_entropy = F.cross_entropy(input=logits, target=torch.argmax(labels, dim=1), reduction='none')

        # for item in [logits, labels, logits_abs_diff_targets,
        #      logits_square_diff_targets, sign_logits, cross_entropy]:
        #     print(item.shape)

        logit_targets_features = torch.cat(
            [logits, labels, logits_abs_diff_targets,
             logits_square_diff_targets, sign_logits, cross_entropy.unsqueeze(1)], dim=1).unsqueeze(1)

        self.layer_dict['pred_relational_network'] = BatchRelationalModule(input_shape=logit_targets_features.shape)
        logit_targets_features = self.layer_dict['pred_relational_network'].forward(logit_targets_features)
        processed_feature_list.append(logit_targets_features)

        if self.support_set_feature_shape is not None:
            features = torch.ones(self.support_set_feature_shape)
            self.layer_dict['feature_relational_network'] = BatchRelationalModule(input_shape=features.shape)
            features = self.layer_dict['feature_relational_network'].forward(features)
            processed_feature_list.append(features)

        if self.task_embedding_shape is not None:
            task_embedding = torch.zeros(self.task_embedding_shape)
            task_embed_batched = task_embedding.view(1, -1)
            task_embed_batched = task_embed_batched.repeat(processed_feature_list[0].shape[0], 1)
            processed_feature_list.append(task_embed_batched)

        # print(param_features_batched.shape, logit_targets_features.shape)
        for item in processed_feature_list:
            print('this one', item.shape)

        mixed_features = torch.cat(processed_feature_list, dim=1)

        # feature_sets = [mixed_features]
        # # print(feature_sets.shape)
        # # for i in range(5):
        # #     dilation = 2 ** i
        # #     cur = torch.cat(feature_sets, dim=1)
        # #     self.layer_dict['dilated_conv1d_{}'.format(i)] = nn.Conv1d(in_channels=cur.shape[1],
        # #                                                                out_channels=8, kernel_size=3,
        # #                                                                dilation=dilation, padding=dilation)
        # #     cur = self.layer_dict['dilated_conv1d_{}'.format(i)](cur)
        # #     self.layer_dict['norm_layer_{}'.format(i)] = nn.BatchNorm1d(num_features=cur.shape[1])
        # #     cur = self.layer_dict['norm_layer_{}'.format(i)](cur)
        # #     cur = F.relu(cur)
        # #     feature_sets.append(cur)

        out = mixed_features

        # out = out.view(out.shape[0], -1)

        self.layer_dict['linear_0'] = nn.Linear(in_features=out.shape[1],
                                                out_features=16, bias=False)

        out = self.layer_dict['linear_0'](out)
        out = F.relu(out)

        self.layer_dict['linear_1'] = nn.Linear(in_features=out.shape[1],
                                                out_features=16, bias=False)

        out = self.layer_dict['linear_1'](out)
        out = F.relu(out)

        self.layer_dict['linear_preds'] = nn.Linear(in_features=out.shape[1],
                                                    out_features=1, bias=False)

        out = self.layer_dict['linear_preds'](out)

        out = out.sum()
        print("VGGNetwork build", out.shape)

    def forward(self, logits, target, support_set_features=None, task_embedding=None, return_sum=True):
        """
        Forward propages through the network. If any params are passed then they are used instead of stored params.
        :param x: Input image batch.
        :param num_step: The current inner loop step number
        :param params: If params are None then internal parameters are used. If params are a dictionary with keys the
         same as the layer names then they will be used instead.
        :param training: Whether this is training (True) or eval time.
        :param backup_running_statistics: Whether to backup the running statistics in their backup store. Which is
        then used to reset the stats back to a previous state (usually after an eval loop, when we want to throw away stored statistics)
        :return: Logits of shape b, num_output_classes.
        """

        processed_feature_list = []
        target_one_hot = torch.zeros(logits.shape, device=logits.device).scatter_(1, target.unsqueeze(1), 1.0)

        logits_abs_diff_targets = torch.abs(logits - target_one_hot)

        logits_square_diff_targets = (logits - target_one_hot) ** 2

        sign_logits = torch.sign(logits - target_one_hot)

        cross_entropy = F.cross_entropy(input=logits, target=target, reduction='none')

        logit_targets_features = torch.cat(
            [logits, target_one_hot, logits_abs_diff_targets,
             logits_square_diff_targets, sign_logits, cross_entropy.unsqueeze(1)], dim=1).unsqueeze(1)

        logit_targets_features = self.layer_dict['pred_relational_network'].forward(logit_targets_features)
        processed_feature_list.append(logit_targets_features)

        if support_set_features is not None:
            features = support_set_features
            features = self.layer_dict['feature_relational_network'].forward(features)
            processed_feature_list.append(features.view(features.shape[0], -1))

        if task_embedding is not None:
            task_embed_batched = task_embedding.view(1, -1)
            # if 'preds' in self.conditional_information:
            task_embed_batched = task_embed_batched.repeat(processed_feature_list[0].shape[0], 1)

            processed_feature_list.append(task_embed_batched)

        # for item in processed_feature_list:
        #     print('this one', item.shape)
        mixed_features = torch.cat(processed_feature_list, dim=1)

        out = self.layer_dict['linear_0'](mixed_features)
        out = F.relu(out)

        out = self.layer_dict['linear_1'](out)
        out = F.relu(out)

        out = self.layer_dict['linear_preds'](out)

        if return_sum:
            out = out.sum()

        return out


class ComparatorNetwork(nn.Module):
    def __init__(self, support_set_shape, target_set_shape, num_layers, num_features):
        super(ComparatorNetwork, self).__init__()
        self.support_set_shape = support_set_shape
        self.target_set_shape = target_set_shape
        self.num_layers = num_layers
        self.num_features = num_features
        self.build_block()

    def build_block(self):
        self.layer_dict = nn.ModuleDict()

        x_support = torch.zeros(self.support_set_shape)
        x_target = torch.zeros(self.target_set_shape)

        x_support = F.adaptive_avg_pool2d(x_support, output_size=(5, 5))
        x_target = F.adaptive_avg_pool2d(x_target, output_size=(5, 5))

        print('>>>>>>>>>>>>>>', x_support.shape, x_target.shape)

        self.layer_dict['batch_support_network'] = BatchRelationalModule(input_shape=x_support.shape,
                                                                         use_coordinates=False)
        self.layer_dict['batch_target_network'] = BatchRelationalModule(input_shape=x_target.shape,
                                                                        use_coordinates=False)

        support_set_embedding = self.layer_dict['batch_support_network'].forward(x_support)
        target_set_embedding = self.layer_dict['batch_target_network'].forward(x_target)

        support_set_embedding = support_set_embedding.unsqueeze(0).permute([0, 2, 1])
        target_set_embedding = target_set_embedding.unsqueeze(0).permute([0, 2, 1])

        self.layer_dict['item_support_network'] = BatchRelationalModule(input_shape=support_set_embedding.shape,
                                                                        use_coordinates=False)
        self.layer_dict['item_target_network'] = BatchRelationalModule(input_shape=target_set_embedding.shape,
                                                                       use_coordinates=False)

        support_set_summary_embedding = self.layer_dict['item_support_network'].forward(support_set_embedding)
        target_set_summary_embedding = self.layer_dict['item_target_network'].forward(target_set_embedding)

        diff_embedding = torch.abs(support_set_summary_embedding.view(-1)) - torch.abs(
            target_set_summary_embedding.view(-1))
        mult_embedding = support_set_summary_embedding.view(-1) * target_set_summary_embedding.view(-1)
        cosine_similarity = mult_embedding.sum().view(1)
        eucledian_distance = diff_embedding.sum().view(1)

        combined_features = torch.cat([support_set_summary_embedding.view(-1), target_set_summary_embedding.view(-1),
                                       diff_embedding, mult_embedding, cosine_similarity, eucledian_distance], dim=0)
        out = combined_features.unsqueeze(0)
        for i in range(self.num_layers):
            self.layer_dict['processing_layer_{}'.format(i)] = nn.Linear(in_features=out.shape[1],
                                                                         out_features=self.num_features, bias=False)
            out = self.layer_dict['processing_layer_{}'.format(i)].forward(out)
            out = F.leaky_relu(out)

        self.layer_dict['output_layer'] = nn.Linear(in_features=out.shape[1], out_features=1, bias=False)
        out = self.layer_dict['output_layer'].forward(out).view(-1)

        print(out.shape)

    def forward(self, x_support, x_target):

        x_support = F.adaptive_avg_pool2d(x_support, output_size=(5, 5))
        x_target = F.adaptive_avg_pool2d(x_target, output_size=(5, 5))

        # print(x_support.shape, x_target.shape)

        support_set_embedding = self.layer_dict['batch_support_network'].forward(x_support)
        target_set_embedding = self.layer_dict['batch_target_network'].forward(x_target)

        support_set_embedding = support_set_embedding.unsqueeze(0).permute([0, 2, 1])
        target_set_embedding = target_set_embedding.unsqueeze(0).permute([0, 2, 1])

        support_set_summary_embedding = self.layer_dict['item_support_network'].forward(support_set_embedding)
        target_set_summary_embedding = self.layer_dict['item_target_network'].forward(target_set_embedding)

        diff_embedding = torch.abs(support_set_summary_embedding.view(-1)) - torch.abs(
            target_set_summary_embedding.view(-1))
        mult_embedding = support_set_summary_embedding.view(-1) * target_set_summary_embedding.view(-1)
        cosine_similarity = mult_embedding.sum().view(1)
        eucledian_distance = diff_embedding.sum().view(1)

        combined_features = torch.cat([support_set_summary_embedding.view(-1), target_set_summary_embedding.view(-1),
                                       diff_embedding, mult_embedding, cosine_similarity, eucledian_distance], dim=0)

        out = combined_features.unsqueeze(0)

        for i in range(self.num_layers):
            out = self.layer_dict['processing_layer_{}'.format(i)].forward(out)
            out = F.leaky_relu(out)

        out = self.layer_dict['output_layer'].forward(out).view(-1)

        return out

# x = torch.zeros((32, 3, 5, 5))
#
# module = ComparatorNetwork(support_set_shape=x.shape, target_set_shape=x.shape, num_layers=2, num_features=32)
