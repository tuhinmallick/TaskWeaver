import io
import itertools
import json
from json import JSONDecodeError
from typing import Any, Callable, Dict, Iterator, List, Optional

import ijson
from injector import inject

from taskweaver.logging import TelemetryLogger
from taskweaver.memory import Attachment, Post


class PostTranslator:
    """
    PostTranslator is used to parse the output of the LLM or convert it to a Post object.
    The core function is post_to_raw_text and raw_text_to_post.
    """

    @inject
    def __init__(
        self,
        logger: TelemetryLogger,
    ):
        self.logger = logger

    def raw_text_to_post(
        self,
        llm_output: str,
        send_from: str,
        event_handler: Callable,
        early_stop: Optional[Callable] = None,
        validation_func: Optional[Callable] = None,
    ) -> Post:
        """
        Convert the raw text output of LLM to a Post object.
        :param llm_output_stream:
        :param send_from:
        :param event_handler:
        :param early_stop:
        :return: Post
        """
        # llm_output_list = [token for token in llm_output_stream]  # collect all the llm output via iterator
        # llm_output = "".join(llm_output_list)
        post = Post.create(message=None, send_from=send_from, send_to=None)
        self.logger.info(f"LLM output: {llm_output}")
        for d in self.parse_llm_output_stream([llm_output]):
            type = d["type"]
            value = d["content"]
            if type == "message":
                post.message = value
            elif type == "send_to":
                post.send_to = value
            else:
                post.add_attachment(Attachment.create(type=type, content=value))
            event_handler(type, value)

            if early_stop is not None and early_stop(type, value):
                break

        if post.send_to is not None:
            event_handler(f"{post.send_from}->{post.send_to}", post.message)

        if validation_func is not None:
            validation_func(post)
        return post

    def post_to_raw_text(
        self,
        post: Post,
        content_formatter: Callable[[Attachment[Any]], str] = lambda x: x.content,
        if_format_message: bool = True,
        if_format_send_to: bool = True,
        ignore_types: Optional[List[str]] = None,
    ) -> str:
        """
        Convert a Post object to raw text in the format of LLM output.
        :param post:
        :param content_formatter:
        :param if_format_message:
        :param if_format_send_to:
        :param ignore_types:
        :return: str
        """
        structured_llm = []
        for attachment in post.attachment_list:
            if ignore_types is not None and attachment.type in ignore_types:
                continue
            attachments_dict = {
                "type": attachment.type,
                "content": content_formatter(attachment),
            }
            structured_llm.append(attachments_dict)
        if if_format_send_to:
            structured_llm.append({"type": "send_to", "content": post.send_to})
        if if_format_message:
            structured_llm.append({"type": "message", "content": post.message})
        structured_llm = {"response": structured_llm}
        return json.dumps(structured_llm)

    def parse_llm_output(self, llm_output: str) -> List[Dict]:
        try:
            structured_llm_output = json.loads(llm_output)["response"]
            assert isinstance(structured_llm_output, list), "LLM output should be a list object"
            return structured_llm_output
        except (JSONDecodeError, AssertionError) as e:
            self.logger.error(f"Failed to parse LLM output due to {str(e)}. LLM output:\n {llm_output}")
            raise e

    def parse_llm_output_stream(
        self,
        llm_output: Iterator[str],
    ) -> Iterator[Dict]:
        json_data_stream = io.StringIO("".join(itertools.chain(llm_output)))
        parser = ijson.parse(json_data_stream)
        element = {}
        try:
            for prefix, event, value in parser:
                if prefix == "response.item" and event == "map_key" and value == "type":
                    element["type"] = None
                elif prefix == "response.item.type" and event == "string":
                    element["type"] = value
                elif prefix == "response.item" and event == "map_key" and value == "content":
                    element["content"] = None
                elif prefix == "response.item.content" and event == "string":
                    element["content"] = value

                if len(element) == 2 and None not in element.values():
                    yield element
                    element = {}
        except ijson.JSONError as e:
            self.logger.warning(f"Failed to parse LLM output stream due to JSONError: {str(e)}")
