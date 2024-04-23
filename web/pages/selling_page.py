#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2024.1.28
# @Author  : HinGwenWong

"""This script refers to the dialogue example of streamlit, the interactive
generation code of chatglm2 and transformers.

We mainly modified part of the code logic to adapt to the
generation of our model.
Please refer to these links below for more information:
    1. streamlit chat example:
        https://docs.streamlit.io/knowledge-base/tutorials/build-conversational-apps
    2. chatglm2:
        https://github.com/THUDM/ChatGLM2-6B
    3. transformers:
        https://github.com/huggingface/transformers
Please run with the command `streamlit run path/to/web_demo.py
    --server.address=0.0.0.0 --server.port 7860`.
Using `python path/to/web_demo.py` may cause unknown problems.
"""
# isort: skip_file
import copy
import random
import warnings
from dataclasses import asdict, dataclass
from typing import Callable, List, Optional

import streamlit as st
import torch
from torch import nn
from transformers.generation.utils import LogitsProcessorList, StoppingCriteriaList
from transformers.utils import logging

from main_page import resize_image


logger = logging.get_logger(__name__)


@dataclass
class GenerationConfig:
    # this config is used for chat to provide more diversity
    max_length: int = 32768
    top_p: float = 0.8
    temperature: float = 0.8
    do_sample: bool = True
    repetition_penalty: float = 1.005


@torch.inference_mode()
def generate_interactive(
    model,
    tokenizer,
    prompt,
    generation_config: Optional[GenerationConfig] = None,
    logits_processor: Optional[LogitsProcessorList] = None,
    stopping_criteria: Optional[StoppingCriteriaList] = None,
    prefix_allowed_tokens_fn: Optional[Callable[[int, torch.Tensor], List[int]]] = None,
    additional_eos_token_id: Optional[int] = None,
    **kwargs,
):
    inputs = tokenizer([prompt], padding=True, return_tensors="pt")
    input_length = len(inputs["input_ids"][0])
    for k, v in inputs.items():
        inputs[k] = v.cuda()
    input_ids = inputs["input_ids"]
    _, input_ids_seq_length = input_ids.shape[0], input_ids.shape[-1]
    if generation_config is None:
        generation_config = model.generation_config
    generation_config = copy.deepcopy(generation_config)
    model_kwargs = generation_config.update(**kwargs)
    bos_token_id, eos_token_id = (  # noqa: F841  # pylint: disable=W0612
        generation_config.bos_token_id,
        generation_config.eos_token_id,
    )
    if isinstance(eos_token_id, int):
        eos_token_id = [eos_token_id]
    if additional_eos_token_id is not None:
        eos_token_id.append(additional_eos_token_id)
    has_default_max_length = kwargs.get("max_length") is None and generation_config.max_length is not None
    if has_default_max_length and generation_config.max_new_tokens is None:
        warnings.warn(
            f"Using 'max_length''s default ({repr(generation_config.max_length)}) \
                to control the generation length. "
            "This behaviour is deprecated and will be removed from the \
                config in v5 of Transformers -- we"
            " recommend using `max_new_tokens` to control the maximum \
                length of the generation.",
            UserWarning,
        )
    elif generation_config.max_new_tokens is not None:
        generation_config.max_length = generation_config.max_new_tokens + input_ids_seq_length
        if not has_default_max_length:
            logger.warn(  # pylint: disable=W4902
                f"Both 'max_new_tokens' (={generation_config.max_new_tokens}) "
                f"and 'max_length'(={generation_config.max_length}) seem to "
                "have been set. 'max_new_tokens' will take precedence. "
                "Please refer to the documentation for more information. "
                "(https://huggingface.co/docs/transformers/main/"
                "en/main_classes/text_generation)",
                UserWarning,
            )

    if input_ids_seq_length >= generation_config.max_length:
        input_ids_string = "input_ids"
        logger.warning(
            f"Input length of {input_ids_string} is {input_ids_seq_length}, "
            f"but 'max_length' is set to {generation_config.max_length}. "
            "This can lead to unexpected behavior. You should consider"
            " increasing 'max_new_tokens'."
        )

    # 2. Set generation parameters if not already defined
    logits_processor = logits_processor if logits_processor is not None else LogitsProcessorList()
    stopping_criteria = stopping_criteria if stopping_criteria is not None else StoppingCriteriaList()

    logits_processor = model._get_logits_processor(
        generation_config=generation_config,
        input_ids_seq_length=input_ids_seq_length,
        encoder_input_ids=input_ids,
        prefix_allowed_tokens_fn=prefix_allowed_tokens_fn,
        logits_processor=logits_processor,
    )

    stopping_criteria = model._get_stopping_criteria(generation_config=generation_config, stopping_criteria=stopping_criteria)
    logits_warper = model._get_logits_warper(generation_config)

    unfinished_sequences = input_ids.new(input_ids.shape[0]).fill_(1)
    scores = None
    while True:
        model_inputs = model.prepare_inputs_for_generation(input_ids, **model_kwargs)
        # forward pass to get next token
        outputs = model(
            **model_inputs,
            return_dict=True,
            output_attentions=False,
            output_hidden_states=False,
        )

        next_token_logits = outputs.logits[:, -1, :]

        # pre-process distribution
        next_token_scores = logits_processor(input_ids, next_token_logits)
        next_token_scores = logits_warper(input_ids, next_token_scores)

        # sample
        probs = nn.functional.softmax(next_token_scores, dim=-1)
        if generation_config.do_sample:
            next_tokens = torch.multinomial(probs, num_samples=1).squeeze(1)
        else:
            next_tokens = torch.argmax(probs, dim=-1)

        # update generated ids, model inputs, and length for next step
        input_ids = torch.cat([input_ids, next_tokens[:, None]], dim=-1)
        model_kwargs = model._update_model_kwargs_for_generation(outputs, model_kwargs, is_encoder_decoder=False)
        unfinished_sequences = unfinished_sequences.mul((min(next_tokens != i for i in eos_token_id)).long())

        output_token_ids = input_ids[0].cpu().tolist()
        output_token_ids = output_token_ids[input_length:]
        for each_eos_token_id in eos_token_id:
            if output_token_ids[-1] == each_eos_token_id:
                output_token_ids = output_token_ids[:-1]
        response = tokenizer.decode(output_token_ids)

        yield response
        # stop when each sentence is finished
        # or if we exceed the maximum length
        if unfinished_sequences.max() == 0 or stopping_criteria(input_ids, scores):
            break


def on_btn_click(*args, **kwargs):
    if kwargs["info"] == "清除对话历史":
        del st.session_state.messages
    elif kwargs["info"] == "返回商品页":
        st.session_state.page_switch = "main_page.py"
    else:
        st.session_state.button_msg = kwargs["info"]


def prepare_generation_config():
    with st.sidebar:
        # 标题
        st.markdown("## 销冠 —— 卖货主播大模型")
        "[销冠 —— 卖货主播大模型 Github repo](https://github.com/PeterH0323/xxx)"

        st.subheader("目前讲解")
        with st.container(height=400, border=True):
            st.subheader(st.session_state.product_name)

            image = resize_image(st.session_state.image_path, max_height=100)
            st.image(image, channels="bgr")

            st.subheader("产品特点", divider="grey")
            st.markdown(st.session_state.hightlight)

            want_to_buy_list = [
                "我打算买了。",
                "我准备入手了。",
                "我决定要买了。",
                "我准备下单了。",
                "我将要购买这款产品。",
                "我准备买下来了。",
                "我准备将这个买下。",
                "我准备要购买了。",
                "我决定买下它。",
                "我准备将其买下。",
            ]
            st.button("加入购物车🛒", on_click=on_btn_click, kwargs={"info": random.choice(want_to_buy_list)})

        # TODO 加入卖货信息
        # 卖出 xxx 个
        # 成交额

        # 模型配置
        st.button("清除对话历史", on_click=on_btn_click, kwargs={"info": "清除对话历史"})
        st.button("返回商品页", on_click=on_btn_click, kwargs={"info": "返回商品页"})
    #     st.markdown("## 模型配置")
    #     max_length = st.slider("Max Length", min_value=8, max_value=32768, value=32768)
    #     top_p = st.slider("Top P", 0.0, 1.0, 0.8, step=0.01)
    #     temperature = st.slider("Temperature", 0.0, 1.0, 0.7, step=0.01)

    max_length = 32768
    top_p = 0.8
    temperature = 0.7
    generation_config = GenerationConfig(max_length=max_length, top_p=top_p, temperature=temperature)

    return generation_config


user_prompt = "<|im_start|>user\n{user}<|im_end|>\n"
robot_prompt = "<|im_start|>assistant\n{robot}<|im_end|>\n"
cur_query_prompt = "<|im_start|>user\n{user}<|im_end|>\n\
    <|im_start|>assistant\n"


def combine_history(prompt, meta_instruction, with_history=True):
    messages = st.session_state.messages
    total_prompt = f"<s><|im_start|>system\n{meta_instruction}<|im_end|>\n"
    if with_history:
        for message in messages:
            cur_content = message["content"]
            if message["role"] == "user":
                cur_prompt = user_prompt.format(user=cur_content)
            elif message["role"] == "robot":
                cur_prompt = robot_prompt.format(robot=cur_content)
            else:
                raise RuntimeError
            total_prompt += cur_prompt
    total_prompt = total_prompt + cur_query_prompt.format(user=prompt)
    return total_prompt


def get_response(prompt, meta_instruction, user_avator, robot_avator, model, tokenizer, generation_config, first_input=False):
    real_prompt = combine_history(prompt, meta_instruction, with_history=True)  # 是否加上历史对话记录
    # print(real_prompt)
    # Add user message to chat history
    if not first_input:
        st.session_state.messages.append({"role": "user", "content": prompt, "avatar": user_avator})

    with st.chat_message("robot", avatar=robot_avator):
        message_placeholder = st.empty()
        for cur_response in generate_interactive(
            model=model,
            tokenizer=tokenizer,
            prompt=real_prompt,
            additional_eos_token_id=92542,
            **asdict(generation_config),
        ):
            # Display robot response in chat message container
            if cur_response == "~":
                continue
            message_placeholder.markdown(cur_response + "▌")
        message_placeholder.markdown(cur_response)
    # Add robot response to chat history
    st.session_state.messages.append(
        {
            "role": "robot",
            "content": cur_response,  # pylint: disable=undefined-loop-variable
            "avatar": robot_avator,
        }
    )
    torch.cuda.empty_cache()


def main(meta_instruction):

    st.set_page_config(
        page_title="Streamer-Sales 销冠",
        page_icon="🛒",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "Get Help": "https://www.extremelycoolapp.com/help",
            "Report a bug": "https://www.extremelycoolapp.com/bug",
            "About": "# This is a Streamer-Sales LLM 销冠--卖货主播大模型",
        },
    )
    # torch.cuda.empty_cache()

    if st.session_state.page_switch != st.session_state.current_page:
        st.switch_page(st.session_state.page_switch)

    user_avator = "../assets/user.png"
    robot_avator = "../assets/logo.png"

    st.title("Streamer-Sales 销冠 —— 卖货主播大模型")

    generation_config = prepare_generation_config()

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display chat messages from history on app rerun
    for message in st.session_state.messages:
        with st.chat_message(message["role"], avatar=message.get("avatar")):
            st.markdown(message["content"])

    if len(st.session_state.messages) == 0:
        # 直接产品介绍
        get_response(
            st.session_state.first_input,
            meta_instruction,
            user_avator,
            robot_avator,
            st.session_state.model,
            st.session_state.tokenizer,
            generation_config,
            first_input=True,
        )

    if "button_msg" not in st.session_state:
        st.session_state.button_msg = "x-x"

    hint_msg = "你好，可以问我任何关于产品的问题"
    if st.session_state.button_msg != "x-x":
        prompt = st.session_state.button_msg
        st.session_state.button_msg = "x-x"
        st.chat_input(hint_msg)
    else:
        prompt = st.chat_input(hint_msg)

    # Accept user input
    if prompt:
        # Display user message in chat message container
        with st.chat_message("user", avatar=user_avator):
            st.markdown(prompt)

        get_response(
            prompt,
            meta_instruction,
            user_avator,
            robot_avator,
            st.session_state.model,
            st.session_state.tokenizer,
            generation_config,
        )


# st.sidebar.page_link("main_page.py", label="商品页")
# st.sidebar.page_link("./pages/selling_page.py", label="主播卖货", disabled=True)

# META_INSTRUCTION = ("现在你是一位金牌带货主播，你的名字叫乐乐喵，你的说话方式是甜美、可爱、熟练使用各种网络热门梗造句、称呼客户为[家人们]。你能够根据产品信息讲解产品并且结合商品信息解答用户提出的疑问。")

st.session_state.current_page = "pages/selling_page.py"

if "model" not in st.session_state or "sales_info" not in st.session_state or st.session_state.sales_info == "":
    st.session_state.page_switch = "main_page.py"
    st.switch_page("main_page.py")

main((st.session_state.sales_info))
