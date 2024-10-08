import time
import uuid
import json
import re
from typing import Any, Callable
from functools import cached_property

from pydantic import BaseModel, PrivateAttr, computed_field
import splunklib.client as splunklib                                                                # type: ignore
from splunklib.binding import HTTPError, ResponseReader                                             # type: ignore
from splunklib.data import Record                                                                   # type: ignore

from contentctl.objects.config import test_common, Infrastructure
from contentctl.objects.detection import Detection
from contentctl.objects.correlation_search import ResultIterator, get_logger

# TODO (cmcginley): remove this logger
logger = get_logger()


class ContentVersioningService(BaseModel):
    global_config: test_common
    infrastructure: Infrastructure
    service: splunklib.Service
    detections: list[Detection]

    _cms_main_job: splunklib.Job | None = PrivateAttr(default=None)

    class Config:
        arbitrary_types_allowed = True

    @computed_field
    @property
    def setup_functions(self) -> list[tuple[Callable[[], None], str]]:
        return [
            (self.activate_versioning, "Activating Content Versioning"),
            (self.wait_for_cms_main, "Waiting for CMS Parser"),
            (self.validate_content_against_cms, "Validating Against CMS"),
        ]

    def _query_content_versioning_service(self, method: str, body: dict[str, Any] = {}) -> Record:
        """
        Queries the SA-ContentVersioning service. Output mode defaults to JSON.

        :param method: HTTP request method (e.g. GET)
        :type method: str
        :param body: the payload/data/body of the request
        :type body: dict[str, Any]

        :returns: a splunklib Record object (wrapper around dict) indicating the response
        :rtype: :class:`splunklib.data.Record`
        """
        # Add output mode to body
        if "output_mode" not in body:
            body["output_mode"] = "json"

        # Query the content versioning service
        try:
            response = self.service.request(                                                     # type: ignore
                method=method,
                path_segment="configs/conf-feature_flags/general",
                body=body,
                app="SA-ContentVersioning"
            )
        except HTTPError as e:
            # Raise on any HTTP errors
            raise HTTPError(
                f"Error querying content versioning service: {e}"
            ) from e

        return response

    @property
    def is_versioning_activated(self) -> bool:
        """
        Indicates whether the versioning service is activated or not

        :returns: a bool indicating if content versioning is activated or not
        :rtype: bool
        """
        # Query the SA-ContentVersioning service for versioning status
        response = self._query_content_versioning_service(method="GET")

        # Grab the response body and check for errors
        if "body" not in response:
            raise KeyError(
                f"Cannot retrieve versioning status, 'body' was not found in JSON response: {response}"
            )
        body: Any = response["body"]                                                             # type: ignore
        if not isinstance(body, ResponseReader):
            raise ValueError(
                "Cannot retrieve versioning status, value at 'body' in JSON response had an unexpected"
                f" type: expected '{ResponseReader}', received '{type(body)}'"
            )

        # Read the JSON and parse it into a dictionary
        json_ = body.readall()
        try:
            data = json.loads(json_)
        except json.JSONDecodeError as e:
            raise ValueError(f"Unable to parse response body as JSON: {e}") from e

        # Find the versioning_activated field and report any errors
        try:
            for entry in data["entry"]:
                if entry["name"] == "general":
                    return bool(int(entry["content"]["versioning_activated"]))
        except KeyError as e:
            raise KeyError(
                "Cannot retrieve versioning status, unable to versioning status using the expected "
                f"keys: {e}"
            ) from e
        raise ValueError(
            "Cannot retrieve versioning status, unable to find an entry matching 'general' in the "
            "response."
        )

    def activate_versioning(self) -> None:
        """
        Activate the content versioning service
        """
        # TODO (cmcginley): add conditional logging s.t. this check only happens when integration
        #   testing is enabled AND ES is at least version 8.0, AND mode is `all`
        # Post to the SA-ContentVersioning service to set versioning status
        self._query_content_versioning_service(
            method="POST",
            body={
                "versioning_activated": True
            }
        )

        # Confirm versioning has been enabled
        if not self.is_versioning_activated:
            raise Exception("Something went wrong, content versioning is still disabled.")

    @computed_field
    @cached_property
    def cms_fields(self) -> list[str]:
        """
        Property listing the fields we want to pull from the cms_main index

        :returns: a list of strings, the fields we want
        :rtype: list[str]
        """
        return [
            "app_name",
            f"action.{self.global_config.app.label.lower()}.full_search_name",
            "detection_id",
            "version",
            "action.correlationsearch.label",
            "sourcetype"
        ]

    @property
    def is_cms_parser_enabled(self) -> bool:
        """
        Indicates whether the cms_parser mod input is enabled or not.

        :returns: a bool indicating if cms_parser mod input is activated or not
        :rtype: bool
        """
        # Get the data input entity
        cms_parser = self.service.input("data/inputs/cms_parser/main")                              # type: ignore

        # Convert the 'disabled' field to an int, then a bool, and then invert to be 'enabled'
        return not bool(int(cms_parser.content["disabled"]))                                        # type: ignore

    def force_cms_parser(self) -> None:
        """
        Force the cms_parser to being it's run being disabling and re-enabling it.
        """
        # Get the data input entity
        cms_parser = self.service.input("data/inputs/cms_parser/main")                              # type: ignore

        # Disable and re-enable
        cms_parser.disable()
        cms_parser.enable()

        # Confirm the cms_parser is enabled
        if not self.is_cms_parser_enabled:
            raise Exception("Something went wrong, cms_parser is still disabled.")

    def wait_for_cms_main(self) -> None:
        """
        Checks the cms_main index until it has the expected number of events, or it times out.
        """
        # Force the cms_parser to start parsing our savedsearches.conf
        self.force_cms_parser()

        # Set counters and limits for out exp. backoff timer
        elapsed_sleep_time = 0
        num_tries = 0
        time_to_sleep = 2**num_tries
        max_sleep = 300

        # Loop until timeout
        while elapsed_sleep_time < max_sleep:
            # Sleep, and add the time to the elapsed counter
            logger.info(f"Waiting {time_to_sleep} for cms_parser to finish")
            time.sleep(time_to_sleep)
            elapsed_sleep_time += time_to_sleep
            logger.info(
                f"Checking cms_main (attempt #{num_tries + 1} - {elapsed_sleep_time} seconds elapsed of "
                f"{max_sleep} max)"
            )

            # Check if the number of CMS events matches or exceeds the number of detections
            if self.get_num_cms_events() >= len(self.detections):
                logger.info(
                    f"Found {self.get_num_cms_events(use_cache=True)} events in cms_main which "
                    f"meets or exceeds the expected {len(self.detections)}."
                )
                break

            # Update the number of times we've tried, and increment the time to sleep
            num_tries += 1
            time_to_sleep = 2**num_tries

            # If the computed time to sleep will exceed max_sleep, adjust appropriately
            if (elapsed_sleep_time + time_to_sleep) > max_sleep:
                time_to_sleep = max_sleep - elapsed_sleep_time

    def _query_cms_main(self, use_cache: bool = False) -> splunklib.Job:
        """
        Queries the cms_main index, optionally appending the provided query suffix.

        :param use_cache: a flag indicating whether the cached job should be returned
        :type use_cache: bool

        :returns: a search Job entity
        :rtype: :class:`splunklib.client.Job`
        """
        # Use the cached job if asked to do so
        if use_cache:
            if self._cms_main_job is not None:
                return self._cms_main_job
            raise Exception(
                "Attempting to return a cached job against the cms_main index, but no job has been"
                " cached yet."
            )

        # Construct the query looking for CMS events matching the content app name
        query = (
            f"search index=cms_main app_name=\"{self.global_config.app.appid}\" | "
            f"fields {', '.join(self.cms_fields)}"
        )
        logger.debug(f"Query on cms_main: {query}")

        # Get the job as a blocking operation, set the cache, and return
        self._cms_main_job = self.service.search(query, exec_mode="blocking")                       # type: ignore
        return self._cms_main_job

    def get_num_cms_events(self, use_cache: bool = False) -> int:
        """
        Gets the number of matching events in the cms_main index

        :param use_cache: a flag indicating whether the cached job should be returned
        :type use_cache: bool

        :returns: the count of matching events
        :rtype: int
        """
        # Query the cms_main index
        job = self._query_cms_main(use_cache=use_cache)

        # Convert the result count to an int
        return int(job["resultCount"])

    def validate_content_against_cms(self) -> None:
        """
        Using the cms_main index, validate content against the index to ensure our
        savedsearches.conf is compatible with ES content versioning features. **NOTE**: while in
        the future, this function may validate more types of content, currently, we only validate
        detections against the cms_main index.
        """
        # Get the cached job and result count
        result_count = self.get_num_cms_events(use_cache=True)
        job = self._query_cms_main(use_cache=True)

        # Create a running list of validation errors
        exceptions: list[Exception] = []

        # Generate an error for the count mismatch
        if result_count != len(self.detections):
            msg = (
                f"Expected {len(self.detections)} matching events in cms_main, but "
                f"found {result_count}."
            )
            logger.error(msg)
            exceptions.append(Exception(msg))
        logger.info(
            f"Expecting {len(self.detections)} matching events in cms_main, "
            f"found {result_count}."
        )

        # Init some counters and a mapping of detections to their names
        count = 100
        offset = 0
        remaining_detections = {x.name: x for x in self.detections}
        matched_detections: dict[str, Detection] = {}

        # Iterate over the results until we've gone through them all
        while offset < result_count:
            iterator = ResultIterator(
                job.results(                                                                        # type: ignore
                    output_mode="json",
                    count=count,
                    offset=offset
                )
            )

            # Iterate over the currently fetched results
            for cms_event in iterator:
                # Increment the offset for each result
                offset += 1

                # Get the name of the search in the CMS event and attempt to use pattern matching
                # to strip the prefix and suffix used for the savedsearches.conf name so we can
                # compare to the detection
                cms_entry_name = cms_event["sourcetype"]
                logger.info(f"{offset}: Matching cms_main entry '{cms_entry_name}' against detections")
                ptrn = re.compile(r"^" + self.global_config.app.label + r" - (?P<cms_entry_name>.+) - Rule$")
                match = ptrn.match(cms_event["sourcetype"])

                # Report any errors extracting the detection name from the longer rule name
                if match is None:
                    msg = (
                        f"Entry in cms_main ('{cms_entry_name}') did not match the expected naming "
                        "scheme; cannot compare to our detections."
                    )
                    logger.error(msg)
                    exceptions.append(Exception(msg))
                    continue

                # Extract the detection name if matching was successful
                cms_entry_name = match.group("cms_entry_name")

                # If CMS entry name matches one of the detections already matched, we've got an
                # unexpected repeated entry
                if cms_entry_name in matched_detections:
                    msg = (
                        f"Detection '{cms_entry_name}' appears more than once in the cms_main "
                        "index."
                    )
                    logger.error(msg)
                    exceptions.append(Exception(msg))
                    continue

                # Iterate over the detections and compare the CMS entry name against each
                result_matches_detection = False
                for detection_name in remaining_detections:
                    # If we find a match, break this loop, set the found flag and move the detection
                    # from those that still need to matched to those already matched
                    if cms_entry_name == detection_name:
                        logger.info(
                            f"{offset}: Succesfully matched cms_main entry against detection "
                            f"('{detection_name}')!"
                        )

                        # Validate other fields of the cms_event against the detection
                        exception = self.validate_detection_against_cms_event(
                            cms_event,
                            remaining_detections[detection_name]
                        )

                        # Save the exception if validation failed
                        if exception is not None:
                            exceptions.append(exception)

                        # Delete the matched detection and move it to the matched list
                        result_matches_detection = True
                        matched_detections[detection_name] = remaining_detections[detection_name]
                        del remaining_detections[detection_name]
                        break

                # Generate an exception if we couldn't match the CMS main entry to a detection
                if result_matches_detection is False:
                    msg = (
                        f"Could not match entry in cms_main for ('{cms_entry_name}') against any "
                        "of the expected detections."
                    )
                    logger.error(msg)
                    exceptions.append(Exception(msg))

        # If we have any remaining detections, they could not be matched against an entry in
        # cms_main and there may have been a parsing issue with savedsearches.conf
        if len(remaining_detections) > 0:
            # Generate exceptions for the unmatched detections
            for detection_name in remaining_detections:
                msg = (
                        f"Detection '{detection_name}' not found in cms_main; there may be an "
                        "issue with savedsearches.conf"
                    )
                logger.error(msg)
                exceptions.append(Exception(msg))

        # Raise exceptions as a group
        if len(exceptions) > 0:
            raise ExceptionGroup(
                "1 or more issues validating our detections against the cms_main index",
                exceptions
            )

        # Else, we've matched/validated all detections against cms_main
        logger.info("Matched and validated all detections against cms_main!")

    def validate_detection_against_cms_event(
            self,
            cms_event: dict[str, Any],
            detection: Detection
    ) -> Exception | None:
        """
        Given an event from the cms_main index and the matched detection, compare fields and look
        for any inconsistencies

        :param cms_event: The event from the cms_main index
        :type cms_event: dict[str, Any]
        :param detection: The matched detection
        :type detection: :class:`contentctl.objects.detection.Detection`

        :return: The generated exception, or None
        :rtype: Exception | None
        """
        # TODO (cmcginley): validate additional fields between the cms_event and the detection

        cms_uuid = uuid.UUID(cms_event["detection_id"])
        full_search_key = f"action.{self.global_config.app.label.lower()}.full_search_name"
        rule_name_from_detection = f"{self.global_config.app.label} - {detection.name} - Rule"

        # Compare the UUIDs
        if cms_uuid != detection.id:
            msg = (
                f"UUID in cms_event ('{cms_uuid}') does not match UUID in detection "
                f"('{detection.id}'): {detection.name}"
            )
            logger.error(msg)
            return Exception(msg)
        elif cms_event["version"] != f"{detection.version}-1":
            # Compare the versions (we append '-1' to the detection version to be in line w/ the
            # internal representation in ES)
            msg = (
                f"Version in cms_event ('{cms_event['version']}') does not match version in "
                f"detection ('{detection.version}-1'): {detection.name}"
            )
            logger.error(msg)
            return Exception(msg)
        elif cms_event[full_search_key] != rule_name_from_detection:
            # Compare the full search name
            msg = (
                f"Full search name in cms_event ('{cms_event[full_search_key]}') "
                f"does not match detection name ('{detection.name}')"
            )
            logger.error(msg)
            return Exception(msg)
        elif cms_event["action.correlationsearch.label"] != f"{self.global_config.app.label} - {detection.name} - Rule":
            # Compare the correlation search label
            msg = (
                f"Correlation search label in cms_event "
                f"('{cms_event['action.correlationsearch.label']}') does not match detection name "
                f"('{detection.name}')"
            )
            logger.error(msg)
            return Exception(msg)
        elif cms_event["sourcetype"] != f"{self.global_config.app.label} - {detection.name} - Rule":
            # Compare the full search name
            msg = (
                f"Sourcetype in cms_event ('{cms_event[f'sourcetype']}') does not match detection "
                f"name ('{detection.name}')"
            )
            logger.error(msg)
            return Exception(msg)

        return None
