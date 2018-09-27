import level4_k210

default_conv_arg = None
default_act_arg = None
default_bn_arg = {
    'load_para': 0,
    'bwsx_base_addr': 0
}
default_pool_arg = {
    'pool_type': 0,  # bypass
}


#
# def q_reverse(qvalue, ranges, mean):
#     return qvalue * ranges + mean
#
#
# def gen_layers_conv_weights(klayers: [level4_k210.K210Layer], x_range, x_mean, w_range, w_mean):
#         arg_add = x_mean * w_mean + w_mean * x_range
#         shr_w, arg_w = log_next_pow_of_2(w_range)
#         shr_x, arg_x = log_next_pow_of_2(x_range)

def signed_to_hex(value, width):
    return hex(int((1 << width) + value) % (1 << width))


def gen_layer_struct(klayer: level4_k210.K210Layer, idx: int):
    reserved = 0
    set_to_zero = 0
    img_ram_size = 2 * 1024 * 1024

    conv_arg = klayer.conv and klayer.conv.to_k210(idx) or default_conv_arg
    act_arg = klayer.act and klayer.act.to_k210() or default_act_arg
    bn_arg = klayer.bn and klayer.bn.to_k210(conv_arg['swsx']) or default_bn_arg
    pool_arg = klayer.pool and klayer.pool.to_k210() or default_pool_arg
    io_arg = klayer.to_k210(idx)

    img_input_size = conv_arg['channel_switch_addr'] * 64 * io_arg['i_ch_num']
    img_output_size = io_arg['wb_channel_switch_addr'] * 64 * io_arg['o_ch_num']
    assert (img_input_size + img_output_size <= img_ram_size)

    interrupt_enabe = {
        'int_en': set_to_zero,
        'ram_flag': reserved,
        'full_add': set_to_zero,
        'depth_wise_layer': conv_arg['depth_wise_layer']
    }
    image_addr = {
        'image_src_addr': '(uint64_t)' + str((0 if not idx & 1 else (img_ram_size - img_input_size))/64),
        'image_dst_addr': '(uint64_t)' + str((0 if idx & 1 else (img_ram_size - img_output_size))/64)
    }
    image_channel_num = {
        'i_ch_num': io_arg['i_ch_num'] - 1,
        'o_ch_num': io_arg['o_ch_num'] - 1,
        'o_ch_num_coef': io_arg['o_ch_num_coef'] - 1,
    }
    image_size = {
        'i_row_wid': conv_arg['i_row_wid'] - 1,
        'i_col_high': conv_arg['i_col_high'] - 1,
        'o_row_wid': io_arg['o_row_wid'] - 1,
        'o_col_high': io_arg['o_col_high'] - 1,
    }
    kernel_pool_type_cfg = {
        'kernel_type': conv_arg['kernel_type'],
        'pad_type': conv_arg['pad_type'],
        'pool_type': pool_arg['pool_type'],
        'first_stride': conv_arg['first_stride'],
        'bypass_conv': 0 if klayer.conv else 1,
        'load_para': bn_arg['load_para'],
        'dma_burst_size': io_arg['dma_burst_size'],
        'pad_value': signed_to_hex(conv_arg['pad_value'], 8),
        'bwsx_base_addr': bn_arg['bwsx_base_addr'],
    }
    kernel_load_cfg = {
        'load_coor': conv_arg['load_coor'],
        'load_time': conv_arg['load_time'] - 1,
        'para_size': conv_arg['para_size'],
        'para_start_addr': conv_arg['para_start_addr'],
    }
    kernel_offset = {
        'coef_column_offset': set_to_zero,
        'coef_row_offset': set_to_zero,
    }
    kernel_calc_type_cfg = {
        'channel_switch_addr': conv_arg['channel_switch_addr'],
        'row_switch_addr': conv_arg['row_switch_addr'],
        'coef_size': reserved,
        'coef_group': conv_arg['coef_group'],
        'load_act': 1 if klayer.act else 0,
        'active_addr': act_arg['active_addr']
    }
    write_back_cfg = {
        'wb_channel_switch_addr': io_arg['wb_channel_switch_addr'],
        'wb_row_switch_addr': io_arg['wb_row_switch_addr'],
        'wb_group': io_arg['wb_group']
    }
    conv_value = {
        'shr_w': conv_arg['shr_w'],
        'shr_x': conv_arg['shr_x'],
        'arg_w': signed_to_hex(conv_arg['arg_w'], 24),
        'arg_x': signed_to_hex(conv_arg['arg_x'], 24),
    }
    conv_value2 = {
        'arg_add': int(round(conv_arg['arg_add'])),
    }
    dma_parameter = {
        'send_data_out': io_arg['send_data_out'],
        'channel_byte_num': io_arg['channel_byte_num'] - 1,
        'dma_total_byte': io_arg['dma_total_byte'] - 1,
    }

    pass
    return {
        'interrupt_enabe': interrupt_enabe,
        'image_addr': image_addr,
        'image_channel_num': image_channel_num,
        'image_size': image_size,
        'kernel_pool_type_cfg': kernel_pool_type_cfg,
        'kernel_load_cfg': kernel_load_cfg,
        'kernel_offset': kernel_offset,
        'kernel_calc_type_cfg': kernel_calc_type_cfg,
        'write_back_cfg': write_back_cfg,
        'conv_value': conv_value,
        'conv_value2': conv_value2,
        'dma_parameter': dma_parameter
    }


def gen_layer_list_struct(klayers: [level4_k210.K210Layer]):
    ret = [
        gen_layer_struct(klayer, idx)
        for klayer, idx in zip(klayers, range(len(klayers)))
    ]
    return ret


def gen_layer_code(dlayer, idx):
    return ('{\n' +
            ',\n'.join([
                ' .' + reg_name + '.data = {\n' +
                ',\n'.join([
                    '  .' + str(k) + ' = ' + (
                        str(v)
                        if str(k) not in ('bwsx_base_addr', 'para_start_addr', 'active_addr')
                        else '0'  # '(uint64_t)' + str(k) + '_' + str(idx)
                    )
                    for k, v in data.items()
                ]) + '\n }'
                for reg_name, data in dlayer.items()
            ]) +
            '\n}')


def gen_bn_code(dlayer, idx):
    bn_list = dlayer['kernel_pool_type_cfg']['bwsx_base_addr']
    bn_code_list = [('{.batchnorm.data = {\n' +
                     '  .norm_mul = ' + str(bn['norm_mul']) + ',\n' +
                     '  .norm_add = ' + str(bn['norm_add']) + ',\n' +
                     '  .norm_shift = ' + str(bn['norm_shift']) + '\n' +
                     '}}') for bn in bn_list]
    return 'cnn_batchnorm_argument_t bwsx_base_addr_' + str(idx) + '[] = {\n' + ',\n'.join(bn_code_list) + '\n};'


def gen_act_code(dlayer, idx):
    act_list = dlayer['kernel_calc_type_cfg']['active_addr']
    active_para = ' .activate_para = {\n' + ',\n'.join([
        '  {{.data = {{.shift_number={dxs}, .y_mul={dy}, .x_start={x} }}}}'.format(
            dxs=item['dxs'], dy=int(item['dy']), x=signed_to_hex(item['x'], 40)
        )
        for item in act_list
    ]) + '\n }'
    bias_list = [item['y'] for item in act_list]
    active_para_bias0 = (' .activate_para_bias0.data = {{\n' + \
                         '  .result_bias = {{{},{},{},{},{},{},{},{}}}\n' + \
                         ' }}').format(*(bias_list[:8]))
    active_para_bias1 = (' .activate_para_bias1.data = {{\n' + \
                         '  .result_bias = {{{},{},{},{},{},{},{},{}}}\n' + \
                         ' }}').format(*(bias_list[8:]))

    return 'cnn_activate_table_t active_addr_' + str(idx) + ' = {\n' + \
           ',\n'.join([active_para, active_para_bias0, active_para_bias1]) + \
           '\n};'


def gen_weights_code(dlayer, idx):
    weights = dlayer['kernel_load_cfg']['para_start_addr']
    weights_data = ', '.join([
        signed_to_hex(item, 16)
        for item in weights
    ])
    return 'uint16_t para_start_addr_{idx}[] = {{{data}}};'.format(idx=idx, data=weights_data)


def gen_layer_list_code(klayers: [level4_k210.K210Layer]):
    structs = gen_layer_list_struct(klayers)

    header_part = '#include "cnn.h"'
    footer_part = '\n'.join([
        'cnn_task_t* cnn_task_init(cnn_task_t* task){',
        ' task->length = sizeof(la)/sizeof(la[0]);',
        '\n '.join([
            'la[{idx}].kernel_pool_type_cfg.data.bwsx_base_addr = (uint64_t)&bwsx_base_addr_{idx};\n'
            'la[{idx}].kernel_calc_type_cfg.data.active_addr = (uint64_t)&active_addr_{idx};\n'
            'la[{idx}].kernel_load_cfg.data.para_start_addr = (uint64_t)&para_start_addr_{idx};'
                .format(idx=idx)
            for idx in range(len(structs))
        ]),
        ' task->layers = la;',
        ' return task;',
        '}'
    ])

    layer_part = 'cnn_layer_argument_t la[] = {' + ',\n'.join([
        gen_layer_code(layer, idx)
        for layer, idx in zip(structs, range(len(structs)))
    ]) + '};'

    bn_part = [
        gen_bn_code(layer, idx)
        for layer, idx in zip(structs, range(len(structs)))
    ]

    act_part = [
        gen_act_code(layer, idx)
        for layer, idx in zip(structs, range(len(structs)))
    ]

    weights_part = [
        gen_weights_code(layer, idx)
        for layer, idx in zip(structs, range(len(structs)))
    ]

    return '\n\n'.join([
        header_part,
        '\n'.join(act_part),
        '\n'.join(bn_part),
        '\n'.join(weights_part),
        layer_part,
        footer_part,
    ])
