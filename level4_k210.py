import math

import level2_layers
import numpy as np


def log_next_pow_of_2(value):
    ret = 0
    while value > 1 or value <= -1:
        value = value / 2
        ret = ret + 1

    # while value < 0.5:
    #     value = value * 2
    #     ret = ret - 1

    return ret, value


class K210Conv:
    def __init__(self, layer, sess, dataset):
        self.layer = layer
        self.depth_wise_layer = isinstance(layer, level2_layers.LayerDepthwiseConvolutional)
        self.tensor = layer.tensor
        self.sess = sess
        self.dataset = dataset
        self.x_range = None
        self.x_mean = None
        self.w_range = None
        self.w_mean = None
        self.output_shape = self.layer.tensor_conv_y.shape

    @staticmethod
    def q(value, ranges, mean):
        return (value - mean) / ranges

    def collection(self):
        batch_x = self.sess.run(self.layer.tensor_conv_x, self.dataset)
        ordered_x = np.sort(np.reshape(batch_x, [np.product(batch_x.shape)]))
        batch_w = self.sess.run(self.layer.tensor_conv_w, self.dataset)
        ordered_w = np.sort(np.reshape(batch_w, [np.product(batch_w.shape)]))

        assert (len(ordered_x) > 10)
        assert (len(ordered_w) > 10)
        # x_min = ordered_x[int(len(ordered_x) * 0.05)]
        # x_max = ordered_x[int(len(ordered_x) * 0.95)]
        x_min = ordered_x[0]  # TODO: fix do not use max-min value
        x_max = ordered_x[-1]

        self.x_range = x_max - x_min
        self.x_mean = x_min
        assert (self.x_range > 0)
        # w_min = ordered_w[int(len(ordered_w) * 0.05)]
        # w_max = ordered_w[int(len(ordered_w) * 0.95)]
        w_min = ordered_w[0]
        w_max = ordered_w[-1]
        self.w_range = w_max - w_min
        self.w_mean = w_min
        assert (self.w_range > 0)

    @staticmethod
    def weights_fill_buffer_33(weights, buf_size):
        reorder = [[[[weights[w][h][i_ch][o_ch]
                      for w in range(int(weights.shape[0]))]
                     for h in range(int(weights.shape[1]))]
                    for i_ch in range(int(weights.shape[2]))]
                   for o_ch in range(int(weights.shape[3]))]

        weights_o_ch_list = [
            np.array(o_ch_weights).flatten()
            for o_ch_weights in reorder
        ]

        weights_shape = weights.shape
        weight_size = 2
        o_ch_weights_size = int(weights_shape[0]) * int(weights_shape[1]) * int(weights_shape[2]) * weight_size
        n = math.floor(buf_size / o_ch_weights_size)
        return K210Layer.batch(weights_o_ch_list, n)

    @staticmethod
    def weights_fill_buffer_11(weights, buf_size):
        reorder = [[[[weights[w][h][i_ch][o_ch]
                      for w in range(int(weights.shape[0]))]
                     for h in range(int(weights.shape[1]))]
                    for i_ch in range(int(weights.shape[2]))]
                   for o_ch in range(int(weights.shape[3]))]

        weights_o_ch_list = [
            [[*batch, None] for batch in K210Layer.batch(np.array(o_ch_weights).flatten(), 8)]
            for o_ch_weights in reorder
        ]

        weights_shape = weights.shape
        weight_size = 2
        o_ch_weights_size = int(weights_shape[0]) * int(weights_shape[1]) * int(weights_shape[2]) * weight_size
        n = math.floor(buf_size / o_ch_weights_size)
        return K210Layer.batch(weights_o_ch_list, n)

    def to_k210(self, idx):
        self.collection()
        weight_buffer_size = 2 * 9 * 4096
        weight_q = self.q(self.layer.weights, self.w_range, self.w_mean)
        weights = self.layer.weights

        input_shape = self.layer.tensor_conv_x.shape
        weights_shape = self.layer.tensor_conv_w.shape
        img_data_size = 1
        weight_data_size = 2
        img_line_size = 64
        img_memory_size = 1024 * 1024 * 2
        weight_cache_row_size = 9 * 2
        weight_cache_mem_size = weight_cache_row_size * 64

        input_row_size = int(input_shape[2]) * img_data_size
        input_channel_size = int(input_shape[1]) * input_row_size
        input_all_size = int(input_shape[3]) * input_channel_size
        output_row_size = int(input_shape[2]) * img_data_size
        output_channel_size = int(input_shape[1]) * output_row_size
        output_all_size = int(input_shape[3]) * output_channel_size
        kernel_size = int(weights_shape[0])
        weight_kernel_size = kernel_size * kernel_size * weight_data_size
        if kernel_size == 1:
            weight_single_output_size = math.ceil(int(weights_shape[0] * weights_shape[1]) / 8) * 9 * weight_data_size
        elif kernel_size == 3:
            weight_single_output_size = weight_kernel_size * int(weights_shape[2])
        else:
            raise "unsupport kernel_size: " + str(kernel_size)

        weight_all_size = weight_single_output_size * int(weights_shape[3])

        # exports:
        bypass_conv = 0
        # img i
        i_row_wid = int(input_shape[2])
        i_col_high = int(input_shape[1])
        coef_group = 1 if i_row_wid > 32 else (2 if i_row_wid > 16 else 4)
        row_switch_addr = math.ceil(i_row_wid / coef_group / 64)
        channel_switch_addr = math.ceil(row_switch_addr * 64 * i_col_high / coef_group / 64)
        # conv
        depth_wise_layer = 1 if self.depth_wise_layer else 0
        kernel_type = {1: 0, 3: 1}[kernel_size]
        pad_type = 0
        load_coor = 1
        load_time = math.ceil(weight_all_size / weight_buffer_size)
        para_size = min(math.floor(weight_buffer_size / weight_single_output_size) * weight_single_output_size,
                        weight_all_size)
        para_start_addr = weight_q
        first_stride = 0 if self.layer.config['stride'] == 1 else 1
        assert (256 > (i_col_high if first_stride == 0 else i_col_high / 2))

        if idx == 0:
            bais_x, scale_x = (0, 256)
        else:
            bais_x, scale_x = (self.x_mean, self.x_range)

        bais_w, scale_w = self.w_mean, self.w_range
        bx_div_sx = bais_x / scale_x
        bw_div_sw = bais_w / scale_w

        magic_hot_fix = 1<<7

        shr_x, arg_x = log_next_pow_of_2(bw_div_sw*magic_hot_fix)
        shr_w, arg_w = log_next_pow_of_2(bx_div_sx*magic_hot_fix)
        arg_add = kernel_size * kernel_size * bw_div_sw * bx_div_sx
        pad_value = -bx_div_sx
        extra_scale = scale_w * scale_x

        return locals()


class K210BN:
    def __init__(self, mean, var, gamma, beta):
        self.mean = mean
        self.var = var
        self.gamma = gamma
        self.beta = beta

    @staticmethod
    def get_bn(scale, bias):
        norm_shift, norm_mul = log_next_pow_of_2(scale)
        return {'norm_mul': hex(int(norm_mul*(1<<16))), 'norm_add': bias, 'norm_shift': norm_shift}

    def to_k210(self, extra_scale=1):
        __tmp_hotfix_magic =  100000000.0 / 3
        __tmp_hotfix_magic_sxsw_base = 1<<32
        scale = extra_scale * self.gamma / self.var * __tmp_hotfix_magic / __tmp_hotfix_magic_sxsw_base
        bias = (self.beta - self.gamma * self.mean / self.var) * __tmp_hotfix_magic

        # print('gamma', self.gamma, 'beta', self.beta, 'mean',self.mean, 'sigma', self.var, 'scale', scale, 'bias', bias)
        load_para = 1
        bwsx_base_addr = [
            self.get_bn(s,b)
            for s,b in zip(scale.tolist(), bias.tolist())
        ]

        return locals()


class K210Act:
    def __init__(self, name):
        self.name = name

    def to_k210(self):
        return {'name': self.name}


class K210Pool:
    def __init__(self, layer, name, size, stride):
        self.name = name
        self.size = size
        self.stride = stride
        self.tensor = layer.tensor_pool

    def to_k210(self):
        if self.name == 'maxpool':
            return {'pool_type': {
                (2, 2): 1,
                (2, 1): 9
            }[(self.size, self.stride)]}
        else:
            return None


class K210Layer:
    def __init__(self):
        self.conv = None
        self.bn = None
        self.act = None
        self.pool = None

    @staticmethod
    def batch(iter, n=1):
        l = len(iter)
        for ndx in range(0, l, n):
            yield iter[ndx:min(ndx + n, l)]

    def to_k210(self):
        if self.pool is not None:
            output_shape = self.pool.tensor.shape
        else:
            output_shape = self.conv.layer.tensor_conv_y.shape

        weights_shape = self.conv.layer.tensor_conv_w.shape
        input_shape = self.conv.layer.tensor_conv_x.shape
        i_row_wid = int(input_shape[1])
        weight_data_size = 2
        img_data_size = 1
        img_line_size = 64
        buf_size = 4096 * 3 * 3 * weight_data_size
        o_ch_weights_size = int(weights_shape[0]) * int(weights_shape[1]) * int(weights_shape[2]) * weight_data_size
        coef_group = 1 if i_row_wid > 32 else (2 if i_row_wid > 16 else 4)

        # io
        i_ch_num = int(weights_shape[2])
        o_ch_num = int(output_shape[3])
        o_ch_num_coef = min(math.floor(buf_size / o_ch_weights_size), int(output_shape[3]))
        # img o
        o_row_wid = int(output_shape[2])
        o_col_high = int(output_shape[1])
        wb_group = 1 if o_row_wid > 32 else (2 if o_row_wid > 16 else 4)
        wb_row_switch_addr = math.ceil(o_row_wid / wb_group / 64)
        wb_channel_switch_addr = math.ceil(wb_row_switch_addr * 64 * o_col_high / wb_group / 64)
        channel_byte_num = wb_channel_switch_addr * int(output_shape[3])

        int_en = 0
        image_src_addr = None
        image_dst_addr = None
        dma_total_byte = int(np.product(self.conv.output_shape[1:]))
        dma_burst_size = 0xf
        send_data_out = 0
        return locals()


def gen_k210_layers(layers: [level2_layers.LayerBase], sess, dataset):
    buffer = list(layers)
    buffer.reverse()
    ret = []

    net = buffer.pop()
    assert (isinstance(net, level2_layers.LayerNet))
    current_shape = int(net.config['width']), int(net.config['height']), int(net.config['channels'])

    while len(buffer) != 0:
        cur_k210 = K210Layer()
        cur_k210.input_shape = buffer[-1].tensor[0].shape

        if isinstance(buffer[-1], level2_layers.LayerConvolutional) \
                or isinstance(buffer[-1], level2_layers.LayerDepthwiseConvolutional):
            conv_layer = buffer.pop()
            # assert (isinstance(conv_layer, level2_layers.LayerConvolutional))
            cur_k210.conv = K210Conv(conv_layer, sess, dataset)
            if int(conv_layer.config['batch_normalize']) == 1:
                cur_k210.bn = K210BN(
                    conv_layer.batch_normalize_moving_mean,
                    conv_layer.batch_normalize_moving_variance,
                    conv_layer.batch_normalize_gamma,
                    conv_layer.batch_normalize_beta
                )
            else:
                bias_shape = conv_layer.bias.shape
                cur_k210.bn = K210BN(0, 1, np.ones(bias_shape), conv_layer.bias)

            cur_k210.act = K210Act(conv_layer.config['activation'])

        if len(buffer) > 0 and isinstance(buffer[-1], level2_layers.LayerMaxpool):
            pool_layer = buffer.pop()
            assert (isinstance(pool_layer, level2_layers.LayerMaxpool))
            cur_k210.pool = K210Pool(pool_layer, 'maxpool', pool_layer.config['size'], pool_layer.config['stride'])

        ret.append(cur_k210)

    return ret