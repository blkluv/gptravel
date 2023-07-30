import os
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

import folium
import numpy as np
import streamlit as st
from openai.error import RateLimitError
from streamlit_folium import st_folium
import streamlit.components.v1 as components

from gptravel.core.io.loggerconfig import logger
from gptravel.core.services.checker import DaysChecker, ExistingDestinationsChecker
from gptravel.core.services.filters import DeparturePlaceFilter
from gptravel.core.services.geocoder import GeoCoder
from gptravel.core.travel_planner import openai_engine
from gptravel.core.travel_planner.prompt import Prompt, PromptFactory
from gptravel.core.travel_planner.token_manager import ChatGptTokenManager
from gptravel.core.travel_planner.travel_engine import TravelPlanJSON
from gptravel.prototype import help as prototype_help
from gptravel.prototype import style as prototype_style
from gptravel.prototype import utils as prototype_utils


def main(
        openai_key: str,
        departure: str,
        destination: str,
        departure_date: datetime,
        return_date: datetime,
        travel_reason: str,
):
    """
     Main function for running travel plan in GPTravel.
     It generates a travel page and display all functionalities of the page.

    Parameters
    ----------
    openai_key : str
        OpenAI API key.
    departure : str
        Departure place.
    destination : str
        Destination place.
    departure_date : datetime
        Departure date.
    return_date : datetime
        Return date.
    travel_reason : str
        Reason for travel.
    """
    try:
        travel_plan_dict, score_dict = _get_travel_plan(
            openai_key=openai_key,
            departure=departure,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            travel_reason=travel_reason,
        )
    except RateLimitError as openai_rate_limit_error:
        st.error(openai_rate_limit_error)

    st.markdown("### Travel Map 🗺️")
    _show_travel_itinerary(travel_plan_dict, destination)

    st.markdown("### Travel Plan 📅")

    st.markdown(
        f"#### Overall Travel Score: \t\t\t\t"
        f"{score_dict.weighted_score * 100:.0f} / 100",
        help=prototype_help.TRAVEL_SCORE_HELP,
    )
    _create_expanders_travel_plan(departure_date, score_dict, travel_plan_dict)

    st.markdown("### Organize Your Trip! 📝")

    st.markdown("#### Flights ✈️")
    components.html(
        f"""
        <div data-skyscanner-widget="SearchWidget"
         data-origin-name={departure}
         data-destination-name={destination}
         data-flight-outbound-date="{departure_date.strftime('%Y-%m-%d')}"
         data-flight-inbound-date="{return_date.strftime('%Y-%m-%d')}"
         ></div>
        <script src="https://widgets.skyscanner.net/widget-server/js/loader.js" ssl=true async></script>
        """,
        height=1600
    )

def _show_travel_itinerary(travel_plan_dict: Dict[str, Any], destination: str) -> None:
    logger.info("Show travel itinerary map: Start")
    travel_plan_cities_names = tuple(
        city for day in travel_plan_dict.keys() for city in travel_plan_dict[day].keys()
    )
    cities_coordinates = (
        prototype_utils.get_cities_coordinates_of_same_country_destionation(
            cities=travel_plan_cities_names, destination=destination
        )
    )
    logger.debug("Computed coordinates = {}".format(cities_coordinates))
    coordinates_array = np.array(
        [[coords[0], coords[1]] for coords in cities_coordinates.values()]
    )
    mean_point_coordinates = np.median(coordinates_array, axis=0)
    zoom_start = 6 if prototype_utils.is_a_country(destination) else 8
    m = folium.Map(location=mean_point_coordinates, zoom_start=zoom_start)

    for city, coordinates in cities_coordinates.items():
        folium.Marker(coordinates, popup=city, tooltip=city).add_to(m)

    # call to render Folium map in Streamlit
    st_folium(m, height=400, width=1000, returned_objects=[])
    logger.info("Show travel itinerary map: Start")


@st.cache_data(show_spinner=False)
def _get_travel_plan(
        openai_key: str,
        departure: str,
        destination: str,
        departure_date: datetime,
        return_date: datetime,
        travel_reason: str,
) -> Tuple[Dict[Any, Any], prototype_utils.TravelPlanScore]:
    """
    Get the travel plan and score dictionary.

    Parameters
    ----------
    openai_key : str
        OpenAI API key.
    departure : str
        Departure place.
    destination : str
        Destination place.
    departure_date : datetime
        Departure date.
    return_date : datetime
        Return date.
    travel_reason : str
        Reason for travel.

    Returns
    -------
    Tuple[Dict[Any, Any], TravelPlanScore]
        A tuple containing the travel plan dictionary and the travel plan score.
    """
    os.environ["OPENAI_API_KEY"] = openai_key
    n_days = (return_date - departure_date).days + 1
    travel_parameters = {
        "departure_place": departure,
        "destination_place": destination,
        "n_travel_days": n_days,
        "travel_theme": travel_reason,
    }
    token_manager = ChatGptTokenManager()
    geocoder = GeoCoder()
    travel_distance = geocoder.location_distance(departure, destination)
    max_number_tokens = token_manager.get_number_tokens(
        n_days=n_days, distance=travel_distance
    )
    travel_plan_json = _get_travel_plan_json(
        travel_parameters=travel_parameters, max_tokens=max_number_tokens
    )
    checker = ExistingDestinationsChecker(geocoder)
    checker.check(travel_plan_json)
    travel_plan_dict = travel_plan_json.travel_plan

    score_dict = prototype_utils.get_score_map(travel_plan_json)

    return travel_plan_dict, score_dict


def _get_travel_plan_json(
        travel_parameters: Dict[str, Any], max_tokens: int
) -> TravelPlanJSON:
    """
    Retrieves the travel plan JSON based on the provided prompt.

    Args:
        travel_parameters (Dict[str, Any]): travel parameters for plan generation.

    Returns:
        TravelPlanJSON: Travel plan JSON.
    """
    logger.info("Building Prompt with travel parameters")
    prompt = _build_prompt(travel_parameters)
    logger.info("Prompt Built successfully")
    logger.info("Generating Travel Plan: Start")
    engine = openai_engine.ChatGPTravelEngine(max_tokens=max_tokens)
    generated_travel_plan = engine.get_travel_plan_json(prompt)
    logger.info("Generating Travel Plan: End")
    travel_filter = DeparturePlaceFilter()
    travel_filter.filter(generated_travel_plan)
    days_checker = DaysChecker()
    if not days_checker.check(generated_travel_plan):
        logger.warning("Completing Travel Plan due to missing days")
        travel_parameters["complention_travel_plan"] = True
        travel_parameters["n_days_to_add"] = (
                generated_travel_plan.n_days - days_checker.travel_days
        )
        travel_parameters["travel_plan"] = generated_travel_plan.travel_plan
        completion_prompt = _build_prompt(travel_parameters)
        generated_travel_plan = engine.get_travel_plan_json(completion_prompt)
    return generated_travel_plan


def _build_prompt(travel_parameters: Dict[str, Any]) -> Prompt:
    """
    Builds the prompt for the travel plan based on the travel parameters.

    Args:
        travel_parameters (Dict[str, Any]): Travel parameters.

    Returns:
        Prompt: Prompt for the travel plan.
    """
    prompt_factory = PromptFactory()
    logger.debug("Building Prompt with parameters = {}".format(travel_parameters))
    prompt = prompt_factory.build_prompt(**travel_parameters)
    return prompt


def _create_expanders_travel_plan(
        departure_date: datetime,
        score_dict: prototype_utils.TravelPlanScore,
        travel_plan_dict: Dict[Any, Any],
) -> None:
    """
    Create expanders for displaying the travel plan.

    Parameters
    ----------
    departure_date : datetime
        Departure date.
    score_dict : prototype_utils.TravelPlanScore
        Score container object.
    travel_plan_dict : Dict[Any, Any]
        Travel plan dictionary.
    """
    for day_num, (day_key, places_dict) in enumerate(travel_plan_dict.items()):
        date_str = (departure_date + timedelta(days=int(day_num))).strftime("%d-%m-%Y")
        expander_day_num = st.expander(f"{day_key} ({date_str})", expanded=True)
        for place, activities in places_dict.items():
            expander_day_num.markdown(f"**{place}**")
            for activity in activities:
                activity_descr = f" {activity}"

                filtered_activities = [
                    items
                    for items in score_dict.score_map["Activities Variety"]["labeled_activities"][activity].items()
                    if items[1] > 0.5
                ]

                if len(filtered_activities) == 0:
                    max_activity = max(
                        score_dict.score_map["Activities Variety"]["labeled_activities"][activity].items(),
                        key=lambda x: x[1]
                    )
                    filtered_activities = [max_activity]

                sorted_filtered_activities = sorted(
                    filtered_activities, key=lambda x: x[1], reverse=True
                )
                activity_label = " ".join(
                    f'<span style="background-color:{prototype_style.COLOR_LABEL_ACTIVITY_DICT[label]}; {prototype_style.LABEL_BOX_STYLE}">\t\t<b>{label.upper()}</b></span>'
                    for label, _ in sorted_filtered_activities
                )
                expander_day_num.markdown(
                    f"- {activity_label} {activity_descr}\n", unsafe_allow_html=True
                )
