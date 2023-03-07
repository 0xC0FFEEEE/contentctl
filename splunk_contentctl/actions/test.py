from dataclasses import dataclass

from splunk_contentctl.objects.test_config import TestConfig

from splunk_contentctl.input.director import DirectorOutputDto
from splunk_contentctl.actions.detection_testing.GitHubService import (
    GithubService,
)

from splunk_contentctl.actions.detection_testing.DetectionTestingManager import (
    DetectionTestingManager,
    DetectionTestingManagerInputDto,
)


from splunk_contentctl.actions.detection_testing.infrastructures.DetectionTestingInfrastructure import (
    DetectionTestingManagerOutputDto,
)


from splunk_contentctl.actions.detection_testing.views.DetectionTestingViewWeb import (
    DetectionTestingViewWeb,
)

from splunk_contentctl.actions.detection_testing.views.DetectionTestingViewCLI import (
    DetectionTestingViewCLI,
)

from splunk_contentctl.actions.detection_testing.views.DetectionTestingViewFile import (
    DetectionTestingViewFile,
)

from argparse import Namespace


MAXIMUM_CONFIGURATION_TIME_SECONDS = 600


@dataclass(frozen=True)
class TestInputDto:
    director_output_dto: DirectorOutputDto
    githubService: GithubService
    config: TestConfig


class TestOutputDto:
    results: list


class Test:
    def execute(self, input_dto: TestInputDto) -> TestOutputDto:

        test_director = input_dto.githubService.get_all_content(
            input_dto.director_output_dto
        )

        output_dto = DetectionTestingManagerOutputDto()
        manager_input_dto = DetectionTestingManagerInputDto(
            config=input_dto.config,
            testContent=test_director,
            views=[
                DetectionTestingViewWeb(config=input_dto.config, sync_obj=output_dto),
                DetectionTestingViewCLI(config=input_dto.config, sync_obj=output_dto),
                DetectionTestingViewFile(config=input_dto.config, sync_obj=output_dto),
            ],
        )
        manager = DetectionTestingManager(
            input_dto=manager_input_dto, output_dto=output_dto
        )

        manager.setup()
        manager.execute()

        t = TestOutputDto()

        return t
        # main(input_dto.config, test_director)
