from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Literal

from pydantic import Field, computed_field, model_serializer, model_validator

from contentctl.objects.story_tags import StoryTags

if TYPE_CHECKING:
    from contentctl.objects.baseline import Baseline
    from contentctl.objects.config import CustomApp
    from contentctl.objects.data_source import DataSource
    from contentctl.objects.detection import Detection
    from contentctl.objects.investigation import Investigation

from contentctl.objects.enums import DetectionStatus
from contentctl.objects.security_content_object import SecurityContentObject


class Story(SecurityContentObject):
    narrative: str = Field(...)
    tags: StoryTags = Field(...)
    status: Literal[DetectionStatus.production, DetectionStatus.deprecated]
    # These are updated when detection and investigation objects are created.
    # Specifically in the model_post_init functions
    detections: List[Detection] = []
    investigations: List[Investigation] = []
    baselines: List[Baseline] = []

    @computed_field
    @property
    def data_sources(self) -> list[DataSource]:
        # Only add a data_source if it does not already exist in the story
        data_source_objects: set[DataSource] = set()
        for detection in self.detections:
            data_source_objects.update(set(detection.data_source_objects))

        return sorted(list(data_source_objects))

    def storyAndInvestigationNamesWithApp(self, app: CustomApp) -> List[str]:
        return [
            detection.get_conf_stanza_name(app) for detection in self.detections
        ] + [
            investigation.get_response_task_name(app)
            for investigation in self.investigations
        ]

    @model_serializer
    def serialize_model(self):
        # Call serializer for parent
        super_fields = super().serialize_model()

        # All fields custom to this model
        model = {
            "narrative": self.narrative,
            "tags": self.tags.model_dump(),
            "detection_names": self.detection_names,
            "investigation_names": self.investigation_names,
            "baseline_names": self.baseline_names,
            "author_company": self.author_company,
            "author_name": self.author_name,
        }
        detections = []
        for detection in self.detections:
            new_detection = {
                "name": detection.name,
                "source": detection.source,
                "type": detection.type,
            }
            if self.tags.mitre_attack_enrichments is not None:
                new_detection["tags"] = {
                    "mitre_attack_enrichments": [
                        {"mitre_attack_technique": enrichment.mitre_attack_technique}
                        for enrichment in detection.tags.mitre_attack_enrichments
                    ]
                }
            detections.append(new_detection)

        model["detections"] = detections
        # Combine fields from this model with fields from parent
        super_fields.update(model)

        # return the model
        return super_fields

    @model_validator(mode="after")
    def setTagsFields(self):
        enrichments = []
        for detection in self.detections:
            enrichments.extend(detection.tags.mitre_attack_enrichments)
        self.tags.mitre_attack_enrichments = list(set(enrichments))

        tactics = []
        for enrichment in self.tags.mitre_attack_enrichments:
            tactics.extend(enrichment.mitre_attack_tactics)
        self.tags.mitre_attack_tactics = set(tactics)

        datamodels = []
        for detection in self.detections:
            datamodels.extend(detection.datamodel)
        self.tags.datamodels = set(datamodels)

        kill_chain_phases = []
        for detection in self.detections:
            kill_chain_phases.extend(detection.tags.kill_chain_phases)
        self.tags.kill_chain_phases = set(kill_chain_phases)

        return self

    @computed_field
    @property
    def author_name(self) -> str:
        match_author = re.search(r"^([^,]+)", self.author)
        if match_author is None:
            return "no"
        else:
            return match_author.group(1)

    @computed_field
    @property
    def author_company(self) -> str:
        match_company = re.search(r",\s?(.*)$", self.author)
        if match_company is None:
            return "no"
        else:
            return match_company.group(1)

    @computed_field
    @property
    def author_email(self) -> str:
        return "-"

    @computed_field
    @property
    def detection_names(self) -> List[str]:
        return [detection.name for detection in self.detections]

    @computed_field
    @property
    def investigation_names(self) -> List[str]:
        return [investigation.name for investigation in self.investigations]

    @computed_field
    @property
    def baseline_names(self) -> List[str]:
        return [baseline.name for baseline in self.baselines]
