import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import openai

from gptravel.core.io.loggerconfig import logger
from gptravel.core.travel_planner.prompt import Prompt
from gptravel.core.travel_planner.travel_engine import TravelEngine, TravelPlanJSON


class OpenAITravelEngine(TravelEngine, ABC):
    def __init__(
        self,
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        frequency_penalty: float,
        presence_penalty: float,
    ) -> None:
        super().__init__()
        assert (presence_penalty >= 0.0) & (presence_penalty <= 2.0)
        assert (frequency_penalty >= 0.0) & (frequency_penalty <= 2.0)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._top_p = top_p
        self._frequency_penalty = frequency_penalty
        self._presence_penalty = presence_penalty
        self._finish_reason: Optional[str] = None
        self._total_tokens: Optional[int] = None

    @abstractmethod
    def _openai_call(self, prompt: Prompt) -> Dict[Any, Any]:
        pass

    def get_travel_plan_json(self, prompt: Prompt) -> TravelPlanJSON:
        response = self._openai_call(prompt)
        message_response = response["choices"][0]["message"]["content"]
        logger.debug("Applying regex on OpenAI GPT response")
        json_parsed_list = self._regex(message_response)
        if len(json_parsed_list) > 1:
            logger.warning("Found multiple json in travel planner response")
        logger.debug("Regex complete successfully")
        try:
            json_object = json.loads(json_parsed_list[0])
        except json.decoder.JSONDecodeError:
            json_object = json.loads(
                r"{}".format(json_parsed_list[0].replace("'", '"'))
            )
        return TravelPlanJSON(
            departure_place=prompt.departure_place,
            destination_place=prompt.destination_place,
            n_days=prompt.n_travel_days,
            travel_plan_json=json_object,
            json_keys_depth_map=prompt.json_keys,
        )

    @property
    def finish_reason(self) -> Optional[str]:
        return self._finish_reason

    @property
    def total_tokens(self) -> Optional[int]:
        return self._total_tokens


class ChatGPTravelEngine(OpenAITravelEngine):
    def __init__(
        self,
        temperature: float = 1.0,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        number_chat_completions: int = 1,
        max_tokens: int = 280,
    ) -> None:
        model = "gpt-3.5-turbo"
        super().__init__(
            model, max_tokens, temperature, top_p, frequency_penalty, presence_penalty
        )
        self._max_tokens = min(self._max_tokens, 4096)  # max token for this engine
        self._n = number_chat_completions

    def _openai_call(self, prompt: Prompt) -> Dict[Any, Any]:
        logger.debug("Fetching travel plan with ChatGpt engine API: Start")
        api_output = openai.ChatCompletion.create(
            model=self._model,
            messages=[
                {"role": "system", "content": "You are an expert travel planner."},
                {"role": "user", "content": prompt.prompt},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            top_p=self._top_p,
            presence_penalty=self._presence_penalty,
            frequency_penalty=self._frequency_penalty,
            n=self._n,
        )
        logger.debug("Fetching travel plan with ChatGpt engine API: Complete")
        self._finish_reason = api_output["choices"][0]["finish_reason"]
        self._total_tokens = api_output["usage"]["total_tokens"]
        logger.debug("OpenAI API: finish reason= {}".format(self._finish_reason))
        logger.debug("OpenAI API: total tokens = {}".format(self._total_tokens))
        return api_output


class CompletionGPTravelEngine(OpenAITravelEngine):
    def __init__(
        self,
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        max_tokens: int = 280,
    ) -> None:
        super().__init__(
            model, max_tokens, temperature, top_p, frequency_penalty, presence_penalty
        )

    def _openai_call(self, prompt: Prompt) -> Dict[Any, Any]:
        logger.debug("Fetching travel plan with ChatGpt engine API: Start")
        api_output = openai.Completion.create(
            model=self._model,
            prompt=prompt.prompt,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            top_p=self._top_p,
            presence_penalty=self._presence_penalty,
            frequency_penalty=self._frequency_penalty,
        )
        logger.debug("Fetching travel plan with ChatGpt engine API: Complete")
        self._finish_reason = api_output["choices"][0]["finish_reason"]
        self._total_tokens = api_output["usage"]["total_tokens"]
        logger.debug("OpenAI API: finish reason= {}".format(self._finish_reason))
        logger.debug("OpenAI API: total tokens = {}".format(self._total_tokens))
        return api_output
