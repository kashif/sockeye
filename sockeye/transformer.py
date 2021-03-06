# Copyright 2017 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may not
# use this file except in compliance with the License. A copy of the License
# is located at
#
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed on
# an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

from typing import Optional

import mxnet as mx
import numpy as np
from . import config
from . import layers


class TransformerConfig(config.Config):

    def __init__(self,
                 model_size: int,
                 attention_heads: int,
                 feed_forward_num_hidden: int,
                 num_layers: int,
                 vocab_size: int,
                 dropout_attention: float,
                 dropout_relu: float,
                 dropout_prepost: float,
                 weight_tying: bool,
                 positional_embedding_type: str,
                 preprocess_sequence: str,
                 postprocess_sequence: str,
                 max_seq_len_source: int,
                 max_seq_len_target: int,
                 conv_config: Optional['ConvolutionalEmbeddingConfig'] = None) -> None:  # type: ignore
        super().__init__()
        self.model_size = model_size
        self.attention_heads = attention_heads
        self.feed_forward_num_hidden = feed_forward_num_hidden
        self.num_layers = num_layers
        self.vocab_size = vocab_size
        self.dropout_attention = dropout_attention
        self.dropout_relu = dropout_relu
        self.dropout_prepost = dropout_prepost
        self.weight_tying = weight_tying
        self.positional_embedding_type = positional_embedding_type
        self.preprocess_sequence = preprocess_sequence
        self.postprocess_sequence = postprocess_sequence
        self.max_seq_len_source = max_seq_len_source
        self.max_seq_len_target = max_seq_len_target
        self.conv_config = conv_config


class TransformerEncoderBlock:
    """
    A transformer encoder block consists self-attention and a feed-forward layer with pre/post process blocks
    in between.
    """

    def __init__(self,
                 config: TransformerConfig,
                 prefix: str) -> None:
        self.pre_self_attention = TransformerProcessBlock(sequence=config.preprocess_sequence,
                                                          num_hidden=config.model_size,
                                                          dropout=config.dropout_prepost,
                                                          prefix="%satt_self_pre_" % prefix)
        self.self_attention = layers.MultiHeadSelfAttention(depth_att=config.model_size,
                                                            heads=config.attention_heads,
                                                            depth_out=config.model_size,
                                                            dropout=config.dropout_attention,
                                                            prefix="%satt_self_" % prefix)
        self.post_self_attention = TransformerProcessBlock(sequence=config.postprocess_sequence,
                                                           num_hidden=config.model_size,
                                                           dropout=config.dropout_prepost,
                                                           prefix="%satt_self_post_" % prefix)

        self.pre_ff = TransformerProcessBlock(sequence=config.preprocess_sequence,
                                              num_hidden=config.model_size,
                                              dropout=config.dropout_prepost,
                                              prefix="%sff_pre_" % prefix)
        self.ff = TransformerFeedForward(num_hidden=config.feed_forward_num_hidden,
                                         num_model=config.model_size,
                                         dropout=config.dropout_relu,
                                         prefix="%sff_" % prefix)
        self.post_ff = TransformerProcessBlock(sequence=config.postprocess_sequence,
                                               num_hidden=config.model_size,
                                               dropout=config.dropout_prepost,
                                               prefix="%sff_post_" % prefix)

    def __call__(self, data: mx.sym.Symbol, data_length: mx.sym.Symbol, length: int) -> mx.sym.Symbol:
        # self-attention
        data_self_att = self.self_attention(self.pre_self_attention(data, None, length), data_length, length)
        data = self.post_self_attention(data_self_att, data, length)

        # feed-forward
        data_ff = self.ff(self.pre_ff(data, None, length), length)
        data = self.post_ff(data_ff, data, length)

        return data


class TransformerDecoderBlock:
    """
    A transformer encoder block consists self-attention, encoder attention, and a feed-forward layer
    with pre/post process blocks in between.
    """

    def __init__(self,
                 config: TransformerConfig,
                 prefix: str) -> None:
        self.pre_self_attention = TransformerProcessBlock(sequence=config.preprocess_sequence,
                                                          num_hidden=config.model_size,
                                                          dropout=config.dropout_prepost,
                                                          prefix="%satt_self_pre_" % prefix)
        self.self_attention = layers.MultiHeadSelfAttention(depth_att=config.model_size,
                                                            heads=config.attention_heads,
                                                            depth_out=config.model_size,
                                                            dropout=config.dropout_attention,
                                                            prefix="%satt_self_" % prefix)
        self.post_self_attention = TransformerProcessBlock(sequence=config.postprocess_sequence,
                                                           num_hidden=config.model_size,
                                                           dropout=config.dropout_prepost,
                                                           prefix="%satt_self_post_" % prefix)

        self.pre_enc_attention = TransformerProcessBlock(sequence=config.preprocess_sequence,
                                                         num_hidden=config.model_size,
                                                         dropout=config.dropout_prepost,
                                                         prefix="%satt_enc_pre_" % prefix)
        self.enc_attention = layers.MultiHeadAttention(depth_att=config.model_size,
                                                       heads=config.attention_heads,
                                                       depth_out=config.model_size,
                                                       dropout=config.dropout_attention,
                                                       prefix="%satt_enc_" % prefix)
        self.post_enc_attention = TransformerProcessBlock(sequence=config.postprocess_sequence,
                                                          num_hidden=config.model_size,
                                                          dropout=config.dropout_prepost,
                                                          prefix="%satt_enc_post_" % prefix)

        self.pre_ff = TransformerProcessBlock(sequence=config.preprocess_sequence,
                                              num_hidden=config.model_size,
                                              dropout=config.dropout_prepost,
                                              prefix="%sff_pre_" % prefix)
        self.ff = TransformerFeedForward(num_hidden=config.feed_forward_num_hidden,
                                         num_model=config.model_size,
                                         dropout=config.dropout_relu,
                                         prefix="%sff_" % prefix)
        self.post_ff = TransformerProcessBlock(sequence=config.postprocess_sequence,
                                               num_hidden=config.model_size,
                                               dropout=config.dropout_prepost,
                                               prefix="%sff_post_" % prefix)

    def __call__(self,
                 target: mx.sym.Symbol,
                 target_lengths: mx.sym.Symbol,
                 target_max_length: int,
                 target_bias: mx.sym.Symbol,
                 source: mx.sym.Symbol,
                 source_lengths: mx.sym.Symbol,
                 source_max_length: int) -> mx.sym.Symbol:

        # self-attention
        target_self_att = self.self_attention(self.pre_self_attention(target, None, target_max_length),
                                              target_lengths,
                                              target_max_length,
                                              bias=target_bias)
        target = self.post_self_attention(target_self_att, target, target_max_length)

        # encoder attention
        target_enc_att = self.enc_attention(self.pre_enc_attention(target, None, target_max_length),
                                            target_max_length,
                                            source,
                                            source_lengths,
                                            source_max_length)
        target = self.post_enc_attention(target_enc_att, target, target_max_length)

        # feed-forward
        target_ff = self.ff(self.pre_ff(target, None, target_max_length), target_max_length)
        target = self.post_ff(target_ff, target, target_max_length)

        return target


class TransformerProcessBlock:
    """
    Block to perform pre/post processing on layer inputs.
    The processing steps are determined by the sequence argument, which can contain one of the three operations:
    n: layer normalization
    r: residual connection
    d: dropout
    """

    def __init__(self,
                 sequence: str,
                 num_hidden: int,
                 dropout: float,
                 prefix: str) -> None:
        self.sequence = sequence
        self.num_hidden = num_hidden
        self.dropout = dropout
        self.prefix = prefix
        self.layer_norm = None
        if "n" in sequence:
            self.layer_norm = layers.LayerNormalization(num_hidden=self.num_hidden, prefix="%snorm" % self.prefix)

    def __call__(self,
                 data: mx.sym.Symbol,
                 prev: Optional[mx.sym.Symbol],
                 length: int) -> mx.sym.Symbol:
        """
        Apply processing sequence to data with optional previous input.

        :param data: Input data. Shape: (batch, length, num_hidden).
        :param prev: Previous data. Shape: (batch, length, num_hidden).
        :param length: Maximum sequence length.
        :return: Processed data. Shape: (batch, length, num_hidden).
        """
        if not self.sequence:
            return data

        if prev is None:
            assert 'r' not in self.sequence, "Residual connection not allowed if no previous value given."

        for step in self.sequence:

            if step == "r":
                data = mx.sym._internal._plus(data, prev, name="%sresidual" % self.prefix)

            elif step == "n":
                data = self._reshape_and_normalize(data, length)

            elif step == "d":
                if self.dropout > 0.0:
                    data = mx.sym.Dropout(data, p=self.dropout, name="%sdropout" % self.prefix)
            else:
                raise ValueError("Unknown step in sequence: %s" % step)

        return data

    def _reshape_and_normalize(self, data: mx.sym.Symbol, length: int) -> mx.sym.Symbol:
        data = mx.sym.reshape(data, shape=(-3, self.num_hidden))
        data = self.layer_norm.normalize(data)
        data = mx.sym.reshape(data, shape=(-4, -1, length, self.num_hidden), name="%snormalized" % self.prefix)
        return data


class TransformerFeedForward:
    """
    Position-wise feed-forward network with ReLU activation.
    """

    def __init__(self,
                 num_hidden: int,
                 num_model: int,
                 dropout: float,
                 prefix: str) -> None:
        self.num_hidden = num_hidden
        self.num_model = num_model
        self.dropout = dropout
        self.prefix = prefix
        self.w_i2h = mx.sym.Variable('%si2h_weight' % prefix)
        self.b_i2h = mx.sym.Variable('%si2h_bias' % prefix)
        self.w_h2o = mx.sym.Variable('%sh2o_weight' % prefix)
        self.b_h2o = mx.sym.Variable('%sh2o_bias' % prefix)

    def __call__(self, x, length) -> mx.sym.Symbol:
        """
        Position-wise feed-forward network with ReLU activation.

        :param x: Symbol of shape (batch_size, seq_len, num_hidden)
        :param length: sequence length
        :return: Symbol of shape (batch_size, seq_len, num_hidden)
        """
        # TODO: use a convolution?
        x = mx.sym.reshape(x, shape=(-3, -1))
        h = mx.sym.FullyConnected(data=x, num_hidden=self.num_hidden, weight=self.w_i2h, bias=self.b_i2h)
        h = mx.sym.Activation(h, act_type="relu")
        if self.dropout > 0.0:
            h = mx.sym.Dropout(h, p=self.dropout)
        y = mx.sym.FullyConnected(data=h, num_hidden=self.num_model, weight=self.w_h2o, bias=self.b_h2o)
        y = mx.sym.reshape(y, shape=(-1, length, self.num_model))
        return y


def get_autoregressive_bias(max_length: int, name: str) -> mx.sym.Symbol:
    """
    Returns bias/mask to ensure position i can only attend to positions <i.

    :param max_length: Sequence length.
    :param name: Name of symbol.
    :return: Bias symbol of shape (1, max_length, max_length).
    """
    return mx.sym.BlockGrad(mx.symbol.Custom(length=max_length,
                                             name=name,
                                             op_type='auto_regressive_bias'))


class AutoRegressiveBias(mx.operator.CustomOp):
    """
    Returns a symbol of shape (1, length, length) with cells above the main diagonal
    set to a large negative value, e.g.
    length=4

    0 1 1 1
    0 0 1 1   * -99999
    0 0 0 1
    0 0 0 0
    """

    def __init__(self, length: int) -> None:
        super().__init__()
        self.bias = self.get_bias(length)

    @staticmethod
    def get_bias(length: int):
        # matrix with lower triangle and main diagonal set to 0, upper triangle set to 1
        upper_triangle = np.triu(np.ones((length, length)), k=1)
        # (1, length, length)
        bias = -99999999. * np.reshape(upper_triangle, (1, length, length))
        return mx.nd.array(bias)

    def forward(self, is_train, req, in_data, out_data, aux):
        self.assign(out_data[0], req[0], self.bias)

    def backward(self, req, out_grad, in_data, out_data, in_grad, aux):
        pass


@mx.operator.register("auto_regressive_bias")
class AutoRegressiveBiasProp(mx.operator.CustomOpProp):

    def __init__(self, length: str) -> None:
        super().__init__()
        self.length = int(length)

    def list_arguments(self):
        return []

    def list_outputs(self):
        return ['output']

    def infer_shape(self, in_shape):
        return [], [(1, self.length, self.length)], []

    def infer_type(self, in_type):
        return [], [np.float32], []

    def create_operator(self, ctx, shapes, dtypes):
        return AutoRegressiveBias(length=self.length)
