# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import re
from typing import Any, Callable, Dict, NoReturn, Optional

import torch
from torch.serialization import MAP_LOCATION
from typing_extensions import TypeAlias

from fairseq2.data.typing import PathLike

CheckpointUpgrader: TypeAlias = Callable[[Dict[str, Any]], Dict[str, Any]]


def load_checkpoint(
    pathname: PathLike,
    model_name: str,
    checkpoint_name: Optional[str] = None,
    map_location: MAP_LOCATION = None,
    restrict: bool = False,
    upgrader: Optional[CheckpointUpgrader] = None,
) -> Dict[str, Any]:
    """Load the checkpoint stored in ``pathname``.

    :param pathname:
        The pathname of the checkpoint.
    :param model_name:
        The name of the associated model.
    :param checkpoint_name:
        The name of the checkpoint.
    :param map_location:
        Same as the ``map_location`` parameter of :meth:`torch.load`.
    :param restrict:
        If ``True``, restricts the Python unpickler to load only tensors,
        primitive types, and dictionaries.
    :param upgrader:
        The callable to which the loaded checkpoint will be passed for further
        processing. Typically used to upgrade legacy checkpoints.
    """

    def raise_error(cause: Exception) -> NoReturn:
        if not checkpoint_name:
            display_name = f"checkpoint of the model '{model_name}'"
        else:
            display_name = f"'{checkpoint_name}' checkpoint of the model '{model_name}'"

        raise RuntimeError(
            f"The load of the {display_name} has failed. Please file a bug report."
        ) from cause

    try:
        checkpoint: Dict[str, Any] = torch.load(
            str(pathname), map_location, weights_only=restrict
        )
    except IOError as ex:
        raise_error(ex)

    if upgrader is not None:
        try:
            checkpoint = upgrader(checkpoint)
        except (KeyError, ValueError) as ex:
            raise_error(ex)

    return checkpoint


def upgrade_fairseq_checkpoint(checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    """Upgrade a checkpoint generated by fairseq.

    .. note::
        This function does not intent to handle all checkpoints generated by
        fairseq. It specificially targets models such as NLLB that have been
        ported to fairseq2.
    """
    old_state_dict = checkpoint["model"]

    new_state_dict = {}

    key_map = _get_fairseq_param_key_map()

    def get_new_key(old_key: str) -> str:
        for old_pttrn, new_repl in key_map.items():
            if (new_key := re.sub(old_pttrn, new_repl, old_key)) != old_key:
                return new_key

        return old_key

    # Convert module keys from fairseq to fairseq2.
    for old_key in old_state_dict.keys():
        new_key = get_new_key(old_key)

        new_state_dict[new_key] = old_state_dict[old_key]

    # Use the built-in version attribute of Module.
    try:
        del new_state_dict["encoder.version"]
    except KeyError:
        pass
    try:
        del new_state_dict["decoder.version"]
    except KeyError:
        pass

    # Positional embeddings don't have to be stored in the checkpoint since
    # we can generate them on-the-fly.
    del new_state_dict["encoder.embed_positions._float_tensor"]
    del new_state_dict["decoder.embed_positions._float_tensor"]

    return {"model": new_state_dict}


def _get_fairseq_param_key_map() -> Dict[str, str]:
    return {
        # fmt: off
        r"^decoder\.layers\.([0-9]+)\.encoder_attn\.":            r"decoder.layers.\1.enc_dec_attn.",
        r"^decoder\.layers\.([0-9]+)\.encoder_attn_layer_norm\.": r"decoder.layers.\1.enc_dec_attn_layer_norm.",
        r"^encoder\.layers\.([0-9]+)\.fc1\.":                     r"encoder.layers.\1.ffn.inner_proj.",
        r"^decoder\.layers\.([0-9]+)\.fc1\.":                     r"decoder.layers.\1.ffn.inner_proj.",
        r"^encoder\.layers\.([0-9]+)\.fc2\.":                     r"encoder.layers.\1.ffn.out_proj.",
        r"^decoder\.layers\.([0-9]+)\.fc2\.":                     r"decoder.layers.\1.ffn.out_proj.",
        r"^encoder\.layers\.([0-9]+)\.final_layer_norm\.":        r"encoder.layers.\1.ffn_layer_norm.",
        r"^decoder\.layers\.([0-9]+)\.final_layer_norm\.":        r"decoder.layers.\1.ffn_layer_norm.",
        r"^encoder\.embed_tokens\.":                              r"encoder_frontend.embed.",
        r"^decoder\.embed_tokens\.":                              r"decoder_frontend.embed.",
        r"^decoder\.output_projection\.":                         r"score_proj.",

        # S2T Transformer
        r"^encoder\.subsample\.conv_layers\.([0-9]+)\.":                    r"encoder_frontend.subsampler.layers.\1.conv.",
        r"^encoder\.transformer_layers\.([0-9]+)\.self_attn_layer_norm\.":  r"encoder.layers.\1.self_attn_layer_norm.",
        r"^encoder\.transformer_layers\.([0-9]+)\.self_attn\.":             r"encoder.layers.\1.self_attn.",
        r"^encoder\.transformer_layers\.([0-9]+)\.final_layer_norm\.":      r"encoder.layers.\1.ffn_layer_norm.",
        r"^encoder\.transformer_layers\.([0-9]+)\.fc1\.":                   r"encoder.layers.\1.ffn.inner_proj.",
        r"^encoder\.transformer_layers\.([0-9]+)\.fc2\.":                   r"encoder.layers.\1.ffn.out_proj.",

        # S2T Conformer
        r"^encoder\.linear\.":                                                   r"encoder_frontend.proj.",
        r"^encoder\.conformer_layers\.([0-9]+)\.ffn(1|2)\.layer_norm\.":         r"encoder.layers.\1.ffn\2_layer_norm.",
        r"^encoder\.conformer_layers\.([0-9]+)\.ffn(1|2)\.w_1\.":                r"encoder.layers.\1.ffn\2.inner_proj.",
        r"^encoder\.conformer_layers\.([0-9]+)\.ffn(1|2)\.w_2\.":                r"encoder.layers.\1.ffn\2.out_proj.",
        r"^encoder\.conformer_layers\.([0-9]+)\.self_attn_layer_norm\.":         r"encoder.layers.\1.self_attn_layer_norm.",
        r"^encoder\.conformer_layers\.([0-9]+)\.self_attn\.linear_q\.":          r"encoder.layers.\1.self_attn.q_proj.",
        r"^encoder\.conformer_layers\.([0-9]+)\.self_attn\.linear_k\.":          r"encoder.layers.\1.self_attn.k_proj.",
        r"^encoder\.conformer_layers\.([0-9]+)\.self_attn\.linear_v\.":          r"encoder.layers.\1.self_attn.v_proj.",
        r"^encoder\.conformer_layers\.([0-9]+)\.self_attn\.linear_out\.":        r"encoder.layers.\1.self_attn.out_proj.",
        r"^encoder\.conformer_layers\.([0-9]+)\.conv_module\.layer_norm\.":      r"encoder.layers.\1.conv_layer_norm.",
        r"^encoder\.conformer_layers\.([0-9]+)\.conv_module\.pointwise_conv1\.": r"encoder.layers.\1.conv.pointwise_conv1.",
        r"^encoder\.conformer_layers\.([0-9]+)\.conv_module\.depthwise_conv\.":  r"encoder.layers.\1.conv.depthwise_conv.",
        r"^encoder\.conformer_layers\.([0-9]+)\.conv_module\.batch_norm\.":      r"encoder.layers.\1.conv.batch_norm.",
        r"^encoder\.conformer_layers\.([0-9]+)\.conv_module\.pointwise_conv2\.": r"encoder.layers.\1.conv.pointwise_conv2.",
        r"^encoder\.conformer_layers\.([0-9]+)\.final_layer_norm\.":             r"encoder.layers.\1.layer_norm.",
        # fmt: on
    }
