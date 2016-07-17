# -*- coding: utf-8 -*-
"""
beam search modules for Neural Walker

@author: hongyuan
"""

import pickle
import time
import numpy
import theano
from theano import sandbox
import theano.tensor as tensor
import os
import scipy.io
from collections import defaultdict
from theano.tensor.shared_randomstreams import RandomStreams
import utils

dtype=theano.config.floatX
#

#TODO: beam search for attention tree2seq with GRU
class BeamSearchNeuralWalker(object):
    '''
    This is a beam search code for Neural Walker
    '''
    def __init__(self, settings):
        print "initializing the beam searcher ... "
        assert (settings['size_beam'] >= 1)
        self.size_beam = settings['size_beam']
        #
        assert(
            settings['path_model'] == None or settings['trained_model'] == None
        )
        #
        if settings['path_model'] != None:
            with open(settings['path_model'], 'rb') as f:
                self.model = pickle.load(f)
        else:
            assert(settings['trained_model']!=None)
            self.model = settings['trained_model']
        #
        # convert float64 to float32
        for param_name in self.model:
            self.model[param_name] = numpy.float32(self.model[param_name])
        #
        self.dim_model = self.model['Emb_enc_forward'].shape[1]
        #
        self.ht_encode = numpy.zeros(
            (self.dim_model, ), dtype=dtype
        )
        self.ct_encode = numpy.zeros(
            (self.dim_model, ), dtype=dtype
        )
        #
        self.scope_att = None
        self.scope_att_times_W = None
        #
        self.beam_list = []
        self.finish_list = []
        #self.normalize_mode = settings['normalize_mode']
        # whether to normalize the cost over length of sequence
        #
        #self.lang2idx = settings['lang2idx']
        self.dim_lang = settings['dim_lang']
        self.map = settings['map']
        self.Emb_lang_sparse = numpy.identity(
            self.dim_lang, dtype=dtype
        )
        #

    def refresh_state(self):
        print "refreshing the states of beam search ... "
        self.ht_encode = numpy.zeros(
            (self.dim_model, ), dtype=dtype
        )
        self.ct_encode = numpy.zeros(
            (self.dim_model, ), dtype=dtype
        )
        #
        self.scope_att = None
        self.scope_att_times_W = None
        #
        self.beam_list = []
        self.finish_list = []

    #
    def sigmoid(self, x):
        return 1 / (1+numpy.exp(-x))
    #
    def set_encoder_forward(self):
        xt_lang_forward = self.model['Emb_enc_forward'][
            self.seq_lang_numpy, :
        ]
        shape_encode = xt_lang_forward.shape
        self.ht_enc_forward = numpy.zeros(
            shape_encode, dtype=dtype
        )
        self.ct_enc_forward = numpy.zeros(
            shape_encode, dtype=dtype
        )
        len_lang = shape_encode[0]
        for time_stamp in range(-1, len_lang-1, 1):
            post_transform = self.model['b_enc_forward'] + numpy.dot(
                numpy.concatenate(
                    (
                        xt_lang_forward[time_stamp+1, :],
                        self.ht_enc_forward[time_stamp, :]
                    ),
                    axis=0
                ),
                self.model['W_enc_forward']
            )
            #
            gate_input_numpy = self.sigmoid(
                post_transform[:self.dim_model]
            )
            gate_forget_numpy = self.sigmoid(
                post_transform[self.dim_model:2*self.dim_model]
            )
            gate_output_numpy = self.sigmoid(
                post_transform[2*self.dim_model:3*self.dim_model]
            )
            gate_pre_c_numpy = numpy.tanh(
                post_transform[3*self.dim_model:]
            )
            self.ct_enc_forward[time_stamp+1, :] = gate_forget_numpy * self.ct_enc_forward[time_stamp, :] + gate_input_numpy * gate_pre_c_numpy
            self.ht_enc_forward[time_stamp+1, :] = gate_output_numpy * numpy.tanh(self.ct_enc_forward[time_stamp+1, :])
            #
        #
    #
    ##
    def set_encoder_backward(self):
        xt_lang_backward = self.model['Emb_enc_backward'][
            self.seq_lang_numpy, :
        ][::-1, :]
        shape_encode = xt_lang_backward.shape
        self.ht_enc_backward = numpy.zeros(
            shape_encode, dtype=dtype
        )
        self.ct_enc_backward = numpy.zeros(
            shape_encode, dtype=dtype
        )
        len_lang = shape_encode[0]
        for time_stamp in range(-1, len_lang-1, 1):
            post_transform = self.model['b_enc_backward'] + numpy.dot(
                numpy.concatenate(
                    (
                        xt_lang_backward[time_stamp+1, :],
                        self.ht_enc_backward[time_stamp, :]
                    ),
                    axis=0
                ),
                self.model['W_enc_backward']
            )
            #
            gate_input_numpy = self.sigmoid(
                post_transform[:self.dim_model]
            )
            gate_forget_numpy = self.sigmoid(
                post_transform[self.dim_model:2*self.dim_model]
            )
            gate_output_numpy = self.sigmoid(
                post_transform[2*self.dim_model:3*self.dim_model]
            )
            gate_pre_c_numpy = numpy.tanh(
                post_transform[3*self.dim_model:]
            )
            self.ct_enc_backward[time_stamp+1, :] = gate_forget_numpy * self.ct_enc_backward[time_stamp, :] + gate_input_numpy * gate_pre_c_numpy
            self.ht_enc_backward[time_stamp+1, :] = gate_output_numpy * numpy.tanh(self.ct_enc_backward[time_stamp+1, :])
            #
        #
    #
    #

    def set_encoder(
        self, seq_lang_numpy, seq_world_numpy
    ):
        #
        self.seq_lang_numpy = seq_lang_numpy
        self.seq_world_numpy = seq_world_numpy
        #
        self.set_encoder_forward()
        self.set_encoder_backward()
        self.scope_att = numpy.concatenate(
            (
                self.Emb_lang_sparse[self.seq_lang_numpy, :],
                self.ht_enc_forward,
                self.ht_enc_backward[::-1, :]
            ),
            axis=1
        )
        self.scope_att_times_W = numpy.dot(
            self.scope_att, self.model['W_att_scope']
        )
        #self.ht_encode = ht_source[:, 0]
        #

    def init_beam(self, pos_start, pos_end):
        print "initialize beam ... "
        item  = {
            'htm1': numpy.copy(self.ht_encode),
            'ctm1': numpy.copy(self.ct_encode),
            'feat_current_position': numpy.copy(
                self.seq_world_numpy[0, :]
            ),
            #
            'pos_current': pos_start,
            'pos_destination': pos_end,
            'list_pos': [numpy.copy(pos_start)]
            #
            'list_idx_action': [],
            'continue': True,
            #
            'cost': 0.00
        }
        self.beam_list.append(item)

    def softmax(self, x):
        # x is a vector
        exp_x = numpy.exp(x - numpy.amax(x))
        return exp_x / numpy.sum(exp_x)

    def decode_step(
        self, feat_current_position,
        htm1_action, ctm1_action
    ):
        #
        xt_action = numpy.dot(
            feat_current_position,
            self.model['Emb_dec']
        )
        # neural attention operations first
        weight_current_step = self.softmax(
            numpy.dot(
                numpy.tanh(
                    numpy.dot(
                        htm1_action, self.model['W_att_target']
                    ) + self.scope_att_times_W
                ),
                self.model['b_att']
            )
        )
        #
        zt_action = numpy.dot(
            weight_current_step,
            self.scope_att
        )
        #
        post_transform = self.model['b_dec'] + numpy.dot(
            numpy.concatenate(
                (
                    xt_action, htm1_action, zt_action
                ),
                axis=0
            ),
            self.model['W_dec']
        )
        #
        gate_input_numpy = self.sigmoid(
            post_transform[:self.dim_model]
        )
        gate_forget_numpy = self.sigmoid(
            post_transform[self.dim_model:2*self.dim_model]
        )
        gate_output_numpy = self.sigmoid(
            post_transform[2*self.dim_model:3*self.dim_model]
        )
        gate_pre_c_numpy = numpy.tanh(
            post_transform[3*self.dim_model:]
        )
        ct_action = gate_forget_numpy * self.ctm1_action + gate_input_numpy * gate_pre_c_numpy
        ht_action = gate_output_numpy * numpy.tanh(ct_action)
        #
        post_transform_prob = numpy.dot(
            xt_action + numpy.dot(
                numpy.concatenate(
                    (ht_action, zt_action), axis=0
                ),
                self.model['W_out_hz']
            ),
            self.model['W_out']
        )
        #
        exp_post_trans = numpy.exp(
            post_transform_prob - numpy.amax(post_transform_prob)
        )
        probt = exp_post_trans / numpy.sum(exp_post_trans)
        log_probt = numpy.log(probt + numpy.float32(1e-8) )
        return xt_action, ht_action, ct_action, probt, log_probt

    def validate_step(self, idx_action, feat_current_position):
        assert(
            idx_action == 3 or idx_action == 2 or idx_action == 1 or idx_action == 0
        )
        if idx_action == 0:
            if feat_current_position[23] > 0.5:
                # 6 + 18 = 24 --> 23
                return False
            else:
                return True
        else:
            return True

    def take_one_step(self, pos_current, idx_action):
        pass

    def get_feat_current_position(self, pos_current):
        pass

    def search_func(self):
        print "search for target ... "
        counter, max_counter = 0, 100
        while ((len(self.finish_list)<self.size_beam) and (counter<max_counter) ):
            new_list = []
            for item in self.beam_list:
                xt_item, ht_item, ct_item, probt_item, log_probt_item = self.decode_step(
                    item['feat_current_position'],
                    item['htm1'], item['ctm1']
                )
                top_k_list = range(probt_item.shape[0])
                for top_idx_action in top_k_list:
                    if self.validate_step(top_idx_action, item['feat_current_position']):
                        new_item = {
                            'htm1': numpy.copy(ht_item),
                            'ctm1': numpy.copy(ct_item),
                            'list_idx_action': [
                                idx for idx in item['list_idx_action']
                            ],
                            'list_pos': [
                                numpy.copy(pos) for pos in item['list_pos']
                            ]
                        }
                        new_item['list_idx_action'].append(
                            top_idx_action
                        )
                        #
                        new_item['pos_current'] = self.take_one_step(
                            item['pos_current'], top_idx_action
                        )
                        #
                        new_item['feat_current_position'] = numpy.copy(
                            self.get_feat_current_position(
                                new_item['pos_current']
                            )
                        )
                        #
                        new_item['list_pos'].append(
                            numpy.copy(new_item['pos_current'])
                        )
                        #
                        if top_idx_action == 3:
                            new_item['continue'] = False
                        else:
                            new_item['continue'] = True
                        #
                        new_item['cost'] = item['cost'] + (-1.0)*log_probt_item[top_idx_action]
                        #
                        new_list.append(new_item)
            '''
            we need to start Here !!!
            And we need to re-check the entire beam search !!!
            '''
            if self.normalize_mode:
                new_list = sorted(
                    new_list, key=lambda x:x['norm_cost']
                )[:self.size_beam]
            else:
                new_list = sorted(
                    new_list, key=lambda x:x['cost']
                )[:self.size_beam]
            self.beam_list = []
            while len(new_list) > 0:
                pop_item = new_list.pop(0)
                if pop_item['continue']:
                    self.beam_list.append(pop_item)
                else:
                    self.finish_list.append(pop_item)
            counter += 1
        #
        if len(self.finish_list) > 0:
            if self.normalize_mode:
                self.finish_list = sorted(
                    self.finish_list, key=lambda x:x['norm_cost']
                )
            else:
                self.finish_list = sorted(
                    self.finish_list, key=lambda x:x['cost']
                )
            while len(self.finish_list) > self.size_beam:
                self.finish_list.pop()
        while len(self.finish_list) < self.size_beam:
            self.finish_list.append(self.beam_list.pop(0))

    def count_response(self):
        print "# of finished responses is ", len(self.finish_list)

    def get_top_target(self):
        print "getting top target as list of token_id ... "
        return self.finish_list[0]['list_idx_token'][1:-1]

    def get_all_gens(self):
        list_seq_as_list = []
        for item in self.finish_list:
            list_seq_as_list.append(
                [idx for idx in item['list_idx_token'][1:-1]]
            )
        #print list_seq_as_list
        return list_seq_as_list
    #
    def get_top_target_score(self):
        print "getting top target score as a value ... "
        if self.normalize_mode:
            return self.finish_list[0]['norm_cost']
        else:
            return self.finish_list[0]['cost']

    def get_all_gens_scores(self):
        list_scores_as_values = []
        for item in self.finish_list:
            if self.normalize_mode:
                score_value = item['norm_cost']
            else:
                score_value = item['cost']
            list_scores_as_values.append(
                score_value
            )
        return list_scores_as_values

    def get_att_weights(self, idx_in_beam):
        #
        list_att_weights = [
            numpy.copy(att_weight) for att_weight in self.finish_list[
                idx_in_beam
            ]['list_att']
        ]
        return list_att_weights
    #
    def get_all_att_weights(self):
        list_all_att_weights = []
        for finish_item in self.finish_list:
            list_all_att_weights.append(
                [
                    numpy.copy(att_weight) for att_weight in finish_item[
                        'list_att'
                    ]
                ]
            )
        return list_all_att_weights

#TODO: beam search for attention tree2seq model with GRU -- version-2
class BeamSearchAttGRUTree2Seq_v2(object):
    '''
    This is a beam search code for tree 2 seq model
    '''
    def __init__(self, settings):
        print "initializing the beam searcher ... "
        assert (settings['size_beam'] >= 1)
        self.size_beam = settings['size_beam']
        #
        assert(
            settings['path_model'] == None or settings['trained_model'] == None
        )
        #
        if settings['path_model'] != None:
            with open(settings['path_model'], 'rb') as f:
                self.model = pickle.load(f)
        else:
            assert(settings['trained_model']!=None)
            self.model = settings['trained_model']
        #
        # convert float64 to float32
        for param_name in self.model:
            self.model[param_name] = numpy.float32(self.model[param_name])
        #
        self.dim_model = self.model['W_recur_source_g12'].shape[0]
        #
        self.ht_encode = numpy.zeros(
            (self.dim_model, ), dtype=dtype
        )
        #
        self.scope_att = None
        self.scope_att_times_W = None
        #
        self.beam_list = []
        self.finish_list = []
        self.normalize_mode = settings['normalize_mode']
        # whether to normalize the cost over length of sequence

    def refresh_state(self):
        print "refreshing the states of beam search ... "
        self.ht_encode = numpy.zeros(
            (self.dim_model, ), dtype=dtype
        )
        #
        self.scope_att = None
        self.scope_att_times_W = None
        #
        self.beam_list = []
        self.finish_list = []

    #
    def sigmoid(self, x):
        return 1 / (1+numpy.exp(-x))
    #

    def set_encoder(
        self, source_seq_numpy,
        source_masks_children_numpy, source_idx_parents_numpy
    ):
        '''
        this function sets the encoder states, given the source_seq_numpy as vector (:,)
        no mask is needed since it is non-batch
        '''
        xt_source = numpy.dot(
            self.model['Emb_source'][source_seq_numpy, :],
            self.model['Emb_tune_source']
        )
        #
        (len_seq, triple_dim_model) = xt_source.shape
        dim_model = int(triple_dim_model / 3)
        shape_encode = (len_seq, dim_model)
        #
        ht_source = numpy.zeros(
            shape_encode, dtype = dtype
        ).transpose()
        # size -- ( dim_model, len_seq )
        # assume ht is same size with xt --
        # both projected to same space
        for idx_current_parent in source_idx_parents_numpy[::-1]:
            mask_children = numpy.copy(
                source_masks_children_numpy[idx_current_parent, :]
            )
            #
            htm1_source = numpy.sum(
                ht_source * mask_children, axis=1
            ) / numpy.sum(mask_children)
            #
            g12_numpy = self.sigmoid(
                numpy.dot(
                    htm1_source,
                    self.model['W_recur_source_g12']
                ) + xt_source[idx_current_parent, :2*dim_model]
            )
            #
            g1_numpy = g12_numpy[:dim_model]
            g2_numpy = g12_numpy[dim_model:]
            #
            g3_numpy = numpy.tanh(
                numpy.dot(
                    (htm1_source * g2_numpy),
                    self.model['W_recur_source_g3']
                ) + xt_source[idx_current_parent, 2*dim_model:]
            )
            #
            ht_source[:, idx_current_parent] = numpy.copy(
                (numpy.float32(1.0)-g1_numpy) * g3_numpy + g1_numpy * htm1_source
            )
            #
        # no multi-input aligner, keep things simple
        self.scope_att = ht_source.transpose()
        #self.scope_att = numpy.concatenate(
        #    (
        #        xt_source,
        #        ht_source.transpose()
        #    ), axis=1
        #)
        #
        self.scope_att_times_W = numpy.dot(
            self.scope_att, self.model['W_att_source']
        )
        #self.ht_encode = ht_source[:, 0]
        #

    def init_beam(self):
        print "initialize beam ... "
        item  = {
            'htm1': numpy.copy(self.ht_encode),
            'input_word_idx': 0,
            'list_idx_token': [0], # 0 -- idx of <EOS>, 1 -- idx of 'UNK'
            'continue': True,
            # save the seq of attention weights
            'list_att': [],
            'length': 1, 'cost': 0.00, 'norm_cost': 0.0
        }
        self.beam_list.append(item)

    def softmax(self, x):
        # x is a vector
        exp_x = numpy.exp(x - numpy.amax(x))
        return exp_x / numpy.sum(exp_x)

    def decode_step(self, input_word_idx, htm1_target):
        #
        # neural attention operations first
        weight_current_step = self.softmax(
            numpy.dot(
                numpy.tanh(
                    numpy.dot(
                        htm1_target, self.model['W_att_target']
                    ) + self.scope_att_times_W
                ),
                self.model['b_att']
            )
        )
        #
        zt_target = numpy.dot(
            weight_current_step,
            self.scope_att
        )
        #
        xt_target = numpy.dot(
            self.model['Emb_target'][input_word_idx, :],
            self.model['Emb_tune_target']
        )
        #
        dim_model = int( xt_target.shape[0] / 3 )
        #
        g12_numpy = self.sigmoid(
            numpy.dot(
                htm1_target, self.model['W_recur_target_g12']
            ) + xt_target[:2*dim_model]
        )
        g1_numpy = g12_numpy[:dim_model]
        g2_numpy = g12_numpy[dim_model:]
        #
        g3_numpy = numpy.tanh(
            numpy.dot(
                (htm1_target * g2_numpy),
                self.model['W_recur_target_g3']
            ) + xt_target[2*dim_model:]
        )
        #
        ht_target = (numpy.float32(1.0)-g1_numpy) * g3_numpy + g1_numpy * htm1_target
        #
        post_transform = numpy.dot(
            numpy.concatenate(
                (ht_target, zt_target), axis=0
            ),
            self.model['W_out']
        )
        exp_post_trans = numpy.exp(
            post_transform - numpy.amax(post_transform)
        )
        probt = exp_post_trans / numpy.sum(exp_post_trans)
        log_probt = numpy.log(probt + numpy.float32(1e-8) )
        return xt_target, ht_target, probt, log_probt, weight_current_step

    def search_func(self):
        print "search for target ... "
        counter, max_counter = 0, 30
        while ((len(self.finish_list)<self.size_beam) and (counter<max_counter) ):
            new_list = []
            for item in self.beam_list:
                xt_item, ht_item, probt_item, log_probt_item, weight_current_step_item = self.decode_step(
                    item['input_word_idx'], item['htm1']
                )
                top_k_list = numpy.argsort(
                    -1.0*log_probt_item
                )[:self.size_beam]
                for top_token_idx in top_k_list:
                    new_item = {
                        'htm1': numpy.copy(ht_item),
                        'list_idx_token': [
                            idx for idx in item['list_idx_token']
                        ],
                        'input_word_idx': top_token_idx,
                        'list_att': [
                            numpy.copy(att_weight) for att_weight in item['list_att']
                        ]
                    }
                    new_item['list_idx_token'].append(
                        top_token_idx
                    )
                    new_item['list_att'].append(
                        numpy.copy(
                            weight_current_step_item
                        )
                    )
                    if top_token_idx == 0:
                        new_item['continue'] = False
                    else:
                        new_item['continue'] = True
                    new_item['length'] = item['length'] + 1
                    new_item['cost'] = item['cost'] + (-1.0)*log_probt_item[top_token_idx]
                    new_item['norm_cost'] = new_item['cost'] / new_item['length']
                    #
                    new_list.append(new_item)
            if self.normalize_mode:
                new_list = sorted(
                    new_list, key=lambda x:x['norm_cost']
                )[:self.size_beam]
            else:
                new_list = sorted(
                    new_list, key=lambda x:x['cost']
                )[:self.size_beam]
            self.beam_list = []
            while len(new_list) > 0:
                pop_item = new_list.pop(0)
                if pop_item['continue']:
                    self.beam_list.append(pop_item)
                else:
                    self.finish_list.append(pop_item)
            counter += 1
        #
        if len(self.finish_list) > 0:
            if self.normalize_mode:
                self.finish_list = sorted(
                    self.finish_list, key=lambda x:x['norm_cost']
                )
            else:
                self.finish_list = sorted(
                    self.finish_list, key=lambda x:x['cost']
                )
            while len(self.finish_list) > self.size_beam:
                self.finish_list.pop()
        while len(self.finish_list) < self.size_beam:
            self.finish_list.append(self.beam_list.pop(0))

    def count_response(self):
        print "# of finished responses is ", len(self.finish_list)

    def get_top_target(self):
        print "getting top target as list of token_id ... "
        return self.finish_list[0]['list_idx_token'][1:-1]

    def get_all_gens(self):
        list_seq_as_list = []
        for item in self.finish_list:
            list_seq_as_list.append(
                [idx for idx in item['list_idx_token'][1:-1]]
            )
        #print list_seq_as_list
        return list_seq_as_list
    #
    def get_top_target_score(self):
        print "getting top target score as a value ... "
        if self.normalize_mode:
            return self.finish_list[0]['norm_cost']
        else:
            return self.finish_list[0]['cost']

    def get_all_gens_scores(self):
        list_scores_as_values = []
        for item in self.finish_list:
            if self.normalize_mode:
                score_value = item['norm_cost']
            else:
                score_value = item['cost']
            list_scores_as_values.append(
                score_value
            )
        return list_scores_as_values

    def get_att_weights(self, idx_in_beam):
        #
        list_att_weights = [
            numpy.copy(att_weight) for att_weight in self.finish_list[
                idx_in_beam
            ]['list_att']
        ]
        return list_att_weights
    #
    def get_all_att_weights(self):
        list_all_att_weights = []
        for finish_item in self.finish_list:
            list_all_att_weights.append(
                [
                    numpy.copy(att_weight) for att_weight in finish_item[
                        'list_att'
                    ]
                ]
            )
        return list_all_att_weights


#
#TODO: beam search for attention tree2seq with LSTM
class BeamSearchAttLSTMTree2Seq(object):
    '''
    This is a beam search code for tree 2 seq model
    This model uses Neural Attention
    The currence unit is LSTM
    '''
    def __init__(self, settings):
        print "initializing the beam searcher ... "
        assert (settings['size_beam'] >= 1)
        assert (settings['path_model'] != None)
        self.size_beam = settings['size_beam']
        with open(settings['path_model'], 'rb') as f:
            self.model = pickle.load(f)
        #
        # convert float64 to float32
        for param_name in self.model:
            self.model[param_name] = numpy.float32(self.model[param_name])
        #
        #
        self.dim_model = self.model['Emb_tune_source'].shape[1]
        # ht_encode and ct_encode is the initial states of decoder
        # do not be misled by the names
        # names are heritated from other models for consistency
        self.ht_encode = numpy.zeros(
            (self.dim_model, ), dtype = dtype
        )
        self.ct_encode = numpy.zeros(
            (self.dim_model, ), dtype = dtype
        )
        #
        self.scope_att = None
        self.scope_att_times_W = None
        #
        self.beam_list = []
        self.finish_list = []
        self.normalize_mode = settings['normalize_mode']
        # whether to normalize the cost over length of sequence

    def refresh_state(self):
        print "refreshing the states of beam search ... "
        self.ht_encode = numpy.zeros(
            (self.dim_model, ), dtype=dtype
        )
        self.ct_encode = numpy.zeros(
            (self.dim_model, ), dtype=dtype
        )
        #
        self.scope_att = None
        self.scope_att_times_W = None
        #
        self.beam_list = []
        self.finish_list = []

    def sigmoid(self, x):
        return 1 / (1+numpy.exp(-x))

    def set_encoder(
        self, source_seq_numpy,
        source_masks_children_numpy, source_idx_parents_numpy
    ):
        '''
        this function sets the encoder states, given the source_seq_numpy as vector (:,)
        no mask is needed since it is non-batch
        '''
        xt_source = numpy.dot(
            self.model['Emb_source'][source_seq_numpy, :],
            self.model['Emb_tune_source']
        )
        shape_encode = xt_source.shape
        ht_source = numpy.zeros(
            shape_encode, dtype = dtype
        ).transpose()
        ct_source = numpy.zeros(
            shape_encode, dtype = dtype
        ).transpose()
        # assume ht is same size with xt --
        # both projected to same space
        for idx_current_parent in source_idx_parents_numpy[::-1]:
            mask_children = numpy.copy(
                source_masks_children_numpy[idx_current_parent, :]
            )
            #
            htm1_source = numpy.sum(
                ht_source * mask_children, axis=1
            ) / numpy.sum(mask_children)
            ctm1_source = numpy.sum(
                ct_source * mask_children, axis=1
            ) / numpy.sum(mask_children)
            #
            post_transform = numpy.dot(
                numpy.concatenate(
                    (
                        xt_source[idx_current_parent, :],
                        htm1_source
                    ), axis=0
                ),
                self.model['W_recur_source']
            )
            #
            gate_input_numpy = self.sigmoid(
                post_transform[:self.dim_model]
            )
            gate_forget_numpy = self.sigmoid(
                post_transform[self.dim_model:2*self.dim_model]
            )
            gate_output_numpy = self.sigmoid(
                post_transform[2*self.dim_model:3*self.dim_model]
            )
            gate_pre_c_numpy = numpy.tanh(
                post_transform[3*self.dim_model:]
            )
            #
            ct_source[:, idx_current_parent] = gate_forget_numpy * ctm1_source + gate_input_numpy * gate_pre_c_numpy
            ht_source[:, idx_current_parent] = gate_output_numpy * ct_source[:, idx_current_parent]
            #
        # for attention model, decoder states start with 0's
        #self.ht_encode = ht_source[:, 0]
        #self.ct_encode = ct_source[:, 0]
        self.scope_att = numpy.concatenate(
            (
                xt_source,
                ht_source.transpose()
            ), axis=1
        )
        self.scope_att_times_W = numpy.dot(
            self.scope_att, self.model['W_att_source']
        )
        #

    def init_beam(self):
        print "initialize beam ... "
        item  = {
            'htm1': numpy.copy(self.ht_encode),
            'ctm1': numpy.copy(self.ct_encode),
            'input_word_idx': 0,
            'list_idx_token': [0], # 0 -- idx of <EOS>, 1 -- idx of 'UNK'
            'continue': True,
            # save the seq of attention weights
            'list_att': [],
            #
            'length': 1, 'cost': 0.00, 'norm_cost': 0.0
        }
        self.beam_list.append(item)

    def softmax(self, x):
        # x is a vector
        exp_x = numpy.exp(x - numpy.amax(x))
        return exp_x / numpy.sum(exp_x)

    def decode_step(self, input_word_idx, htm1_target, ctm1_target):
        #
        # neural attention operations first
        weight_current_step = self.softmax(
            numpy.dot(
                numpy.tanh(
                    numpy.dot(
                        htm1_target, self.model['W_att_target']
                    ) + self.scope_att_times_W
                ),
                self.model['b_att']
            )
        )
        #
        zt_target = numpy.dot(
            weight_current_step,
            self.scope_att
        )
        #
        xt_target = numpy.dot(
            self.model['Emb_target'][input_word_idx, :],
            self.model['Emb_tune_target']
        )
        #
        post_transform = numpy.dot(
            numpy.concatenate(
                (xt_target, htm1_target, zt_target), axis=0
            ),
            self.model['W_recur_target']
        )
        #
        gate_input_numpy = self.sigmoid(
            post_transform[:self.dim_model]
        )
        gate_forget_numpy = self.sigmoid(
            post_transform[self.dim_model:2*self.dim_model]
        )
        gate_output_numpy = self.sigmoid(
            post_transform[2*self.dim_model:3*self.dim_model]
        )
        gate_pre_c_numpy = numpy.tanh(
            post_transform[3*self.dim_model:]
        )
        ct_target = gate_forget_numpy * ctm1_target + gate_input_numpy * gate_pre_c_numpy
        ht_target = gate_output_numpy * ct_target
        #
        post_transform = numpy.dot(
            ht_target, self.model['W_out']
        )
        exp_post_trans = numpy.exp(
            post_transform - numpy.amax(post_transform)
        )
        probt = exp_post_trans / numpy.sum(exp_post_trans)
        log_probt = numpy.log(probt + numpy.float32(1e-8) )
        return xt_target, ht_target, ct_target, probt, log_probt, weight_current_step

    def search_func(self):
        print "search for target ... "
        counter, max_counter = 0, 30
        while ((len(self.finish_list)<self.size_beam) and (counter<max_counter) ):
            new_list = []
            for item in self.beam_list:
                xt_item, ht_item, ct_item, probt_item, log_probt_item, weight_current_step_item = self.decode_step(
                    item['input_word_idx'],
                    item['htm1'], item['ctm1']
                )
                top_k_list = numpy.argsort(
                    -1.0*log_probt_item
                )[:self.size_beam]
                for top_token_idx in top_k_list:
                    new_item = {
                        'htm1': numpy.copy(ht_item),
                        'ctm1': numpy.copy(ct_item),
                        'list_idx_token': [idx for idx in item['list_idx_token']],
                        'input_word_idx': top_token_idx,
                    }
                    new_item['list_idx_token'].append(
                        top_token_idx
                    )
                    new_item['list_att'].append(
                        numpy.copy(
                            weight_current_step_item
                        )
                    )
                    if top_token_idx == 0:
                        new_item['continue'] = False
                    else:
                        new_item['continue'] = True
                    new_item['length'] = item['length'] + 1
                    new_item['cost'] = item['cost'] + (-1.0)*log_probt_item[top_token_idx]
                    new_item['norm_cost'] = new_item['cost'] / new_item['length']
                    #
                    new_list.append(new_item)
            if self.normalize_mode:
                new_list = sorted(
                    new_list, key=lambda x:x['norm_cost']
                )[:self.size_beam]
            else:
                new_list = sorted(
                    new_list, key=lambda x:x['cost']
                )[:self.size_beam]
            self.beam_list = []
            while len(new_list) > 0:
                pop_item = new_list.pop(0)
                if pop_item['continue']:
                    self.beam_list.append(pop_item)
                else:
                    self.finish_list.append(pop_item)
            counter += 1
        #
        if len(self.finish_list) > 0:
            if self.normalize_mode:
                self.finish_list = sorted(
                    self.finish_list, key=lambda x:x['norm_cost']
                )
            else:
                self.finish_list = sorted(
                    self.finish_list, key=lambda x:x['cost']
                )
            while len(self.finish_list) > self.size_beam:
                self.finish_list.pop()
        while len(self.finish_list) < self.size_beam:
            self.finish_list.append(
                self.beam_list.pop(0)
            )

    def count_response(self):
        print "# of finished responses is ", len(self.finish_list)

    def get_top_target(self):
        print "getting top target as list of token_id ... "
        return self.finish_list[0]['list_idx_token'][1:-1]

    def get_all_gens(self):
        list_seq_as_list = []
        for item in self.finish_list:
            list_seq_as_list.append(
                [idx for idx in item['list_idx_token'][1:-1]]
            )
        #print list_seq_as_list
        return list_seq_as_list

    def get_top_target_score(self):
        print "getting top target score as a value ... "
        if self.normalize_mode:
            return self.finish_list[0]['norm_cost']
        else:
            return self.finish_list[0]['cost']

    def get_all_gens_scores(self):
        list_scores_as_values = []
        for item in self.finish_list:
            if self.normalize_mode:
                score_value = item['norm_cost']
            else:
                score_value = item['cost']
            list_scores_as_values.append(
                score_value
            )
        return list_scores_as_values

    def get_att_weights(self, idx_in_beam):
        # given idx_in_beam
        # get its seq of attention weights
        list_att_weights = [
            numpy.copy(att_weight) for att_weight in self.finish_list[
                idx_in_beam
            ]['list_att']
        ]
        return list_att_weights

    def get_all_att_weights(self):
        list_all_att_weights = []
        for finish_item in self.finish_list:
            list_all_att_weights.append(
                [
                    numpy.copy(att_weight) for att_weight in finish_item[
                        'list_att'
                    ]
                ]
            )
        return list_all_att_weights

#
#
#
#TODO: deprecated Beam Search modules ...
#
#
class BeamSearchRNNTree2LSTMSeq(object):
    '''
    This is a beam search code for tree 2 seq model
    '''
    def __init__(self, settings):
        print "initializing the beam searcher ... "
        assert (settings['size_beam'] >= 1)
        assert (settings['path_model'] != None)
        self.size_beam = settings['size_beam']
        #
        assert(
            settings['path_model'] == None or settings['trained_model'] == None
        )
        #
        if settings['path_model'] != None:
            with open(settings['path_model'], 'rb') as f:
                self.model = pickle.load(f)
        else:
            assert(settings['trained_model']!=None)
            self.model = settings['trained_model']
        #
        # convert float64 to float32
        for param_name in self.model:
            self.model[param_name] = numpy.float32(self.model[param_name])
        #
        #
        self.ht_encode = None
        self.ct_encode = None
        self.dim_model = self.model['Emb_tune_source'].shape[1]
        self.beam_list = []
        self.finish_list = []
        self.normalize_mode = settings['normalize_mode']
        # whether to normalize the cost over length of sequence

    def refresh_state(self):
        print "refreshing the states of beam search ... "
        self.ht_encode = None
        self.ct_encode = None
        self.beam_list = []
        self.finish_list = []

    def sigmoid(self, x):
        return 1 / (1+numpy.exp(-x))

    #
    def set_encoder(
        self, source_seq_numpy,
        source_masks_children_numpy, source_idx_parents_numpy
    ):
        '''
        this function sets the encoder states, given the source_seq_numpy as vector (:,)
        '''
        xt_source = numpy.dot(
            self.model['Emb_source'][source_seq_numpy, :],
            self.model['Emb_tune_source']
        )
        shape_encode = xt_source.shape
        ht_source = numpy.zeros(
            shape_encode, dtype = dtype
        ).transpose()
        # assume ht is same size with xt --
        # both projected to same space
        for idx_current_parent in source_idx_parents_numpy[::-1]:
            mask_children = numpy.copy(
                source_masks_children_numpy[idx_current_parent, :]
            )
            ht_source[:, idx_current_parent] = numpy.copy(
                numpy.tanh(
                    numpy.dot(
                        numpy.sum(
                            ht_source * mask_children, axis=1
                        ) / numpy.sum(mask_children),
                        self.model['W_recur_source']
                    ) + xt_source[idx_current_parent, :]
                )
            )
        #
        self.ht_encode = ht_source[:, 0]
        self.ct_encode = numpy.zeros(
            self.ht_encode.shape, dtype=dtype
        )
    #
    #
    def init_beam(self):
        print "initialize beam ... "
        item  = {
            'htm1': numpy.copy(self.ht_encode),
            'ctm1': numpy.copy(self.ct_encode),
            'input_word_idx': 0,
            'list_idx_token': [0], # 0 -- idx of <EOS>, 1 -- idx of 'UNK'
            'continue': True,
            'length': 1, 'cost': 0.00, 'norm_cost': 0.0
        }
        self.beam_list.append(item)

    def decode_step(self, input_word_idx, htm1_target, ctm1_target):
        xt_target = numpy.dot(
            self.model['Emb_target'][input_word_idx, :],
            self.model['Emb_tune_target']
        )
        #
        post_transform = numpy.dot(
            numpy.concatenate(
                (xt_target, htm1_target), axis=0
            ),
            self.model['W_recur_target']
        )
        #
        gate_input_numpy = self.sigmoid(
            post_transform[:self.dim_model]
        )
        gate_forget_numpy = self.sigmoid(
            post_transform[self.dim_model:2*self.dim_model]
        )
        gate_output_numpy = self.sigmoid(
            post_transform[2*self.dim_model:3*self.dim_model]
        )
        gate_pre_c_numpy = numpy.tanh(
            post_transform[3*self.dim_model:]
        )
        ct_target = gate_forget_numpy * ctm1_target + gate_input_numpy * gate_pre_c_numpy
        ht_target = gate_output_numpy * ct_target
        #
        post_transform = numpy.dot(
            ht_target, self.model['W_out']
        )
        exp_post_trans = numpy.exp(
            post_transform - numpy.amax(post_transform)
        )
        probt = exp_post_trans / numpy.sum(exp_post_trans)
        log_probt = numpy.log(probt + numpy.float32(1e-8))
        return xt_target, ht_target, ct_target, probt, log_probt

    def search_func(self):
        print "search for target ... "
        counter, max_counter = 0, 100
        while ((len(self.finish_list)<self.size_beam) and (counter<max_counter) ):
            new_list = []
            for item in self.beam_list:
                xt_item, ht_item, ct_item, probt_item, log_probt_item = self.decode_step(
                    item['input_word_idx'],
                    item['htm1'], item['ctm1']
                )
                top_k_list = numpy.argsort(
                    -1.0*log_probt_item
                )[:self.size_beam]
                for top_token_idx in top_k_list:
                    new_item = {
                        'htm1': numpy.copy(ht_item),
                        'ctm1': numpy.copy(ct_item),
                        'list_idx_token': [idx for idx in item['list_idx_token']],
                        'input_word_idx': top_token_idx
                    }
                    new_item['list_idx_token'].append(top_token_idx)
                    if top_token_idx == 0:
                        new_item['continue'] = False
                    else:
                        new_item['continue'] = True
                    new_item['length'] = item['length'] + 1
                    new_item['cost'] = item['cost'] + (-1.0)*log_probt_item[top_token_idx]
                    new_item['norm_cost'] = new_item['cost'] / new_item['length']
                    #
                    new_list.append(new_item)
            if self.normalize_mode:
                new_list = sorted(
                    new_list, key=lambda x:x['norm_cost']
                )[:self.size_beam]
            else:
                new_list = sorted(
                    new_list, key=lambda x:x['cost']
                )[:self.size_beam]
            self.beam_list = []
            while len(new_list) > 0:
                pop_item = new_list.pop(0)
                if pop_item['continue']:
                    self.beam_list.append(pop_item)
                else:
                    self.finish_list.append(pop_item)
            counter += 1
        #
        if len(self.finish_list) > 0:
            if self.normalize_mode:
                self.finish_list = sorted(
                    self.finish_list, key=lambda x:x['norm_cost']
                )
            else:
                self.finish_list = sorted(
                    self.finish_list, key=lambda x:x['cost']
                )
            while len(self.finish_list) > self.size_beam:
                self.finish_list.pop()
        while len(self.finish_list) < self.size_beam:
            self.finish_list.append(self.beam_list.pop(0))

    def count_response(self):
        print "# of finished responses is ", len(self.finish_list)

    def get_top_target(self):
        print "getting top target as list of token_id ... "
        return self.finish_list[0]['list_idx_token'][1:-1]

    def get_all_gens(self):
        list_seq_as_list = []
        for item in self.finish_list:
            list_seq_as_list.append(
                [idx for idx in item['list_idx_token'][1:-1]]
            )
        #print list_seq_as_list
        return list_seq_as_list

    def get_top_target_score(self):
        print "getting top target score as a value ... "
        if self.normalize_mode:
            return self.finish_list[0]['norm_cost']
        else:
            return self.finish_list[0]['cost']

    def get_all_gens_scores(self):
        list_scores_as_values = []
        for item in self.finish_list:
            if self.normalize_mode:
                score_value = item['norm_cost']
            else:
                score_value = item['cost']
            list_scores_as_values.append(
                score_value
            )
        return list_scores_as_values