import csv
import glob
import logging
import os
import pathlib
import subprocess
import sys
from typing import Union, Tuple
from docker import types
import datetime
import git
import yaml
from git.objects import base

from splunk_contentctl.objects.detection import Detection

from splunk_contentctl.objects.enums import DetectionTestingMode
import random
import pathlib
from splunk_contentctl.helper.utils import Utils

from splunk_contentctl.objects.test_config import TestConfig
from splunk_contentctl.actions.generate import DirectorOutputDto

# Logger
logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
LOGGER = logging.getLogger(__name__)


SSA_PREFIX = "ssa___"


class GithubService:
    def get_detections_selected(self, director: DirectorOutputDto) -> list[Detection]:
        detections_to_test: list[Detection] = []
        if self.config.detections_list is None:
            raise (Exception("detections_list should not be None"))
        detections_set = set(self.config.detections_list)
        for detection in director.detections:
            if detection.file_path in detections_set:
                detections_to_test.append(detection)
                detections_set.remove(detection.file_path)

        if len(detections_set) > 0:
            missing_detections = "\n\t - ".join(detections_set)
            raise (
                Exception(
                    f"Failed to find the following detections for testing:\n\t - {missing_detections}"
                )
            )

        return detections_to_test

    def get_detections_all(self, director: DirectorOutputDto) -> list[Detection]:
        # Assume we don't need to remove anything, like deprecated or experimental from this
        return director.detections

    def get_detections_changed(self, director: DirectorOutputDto) -> list[Detection]:
        if self.repo is None:
            raise (
                Exception(
                    f"Error: self.repo must be initialized before getting changed detections."
                )
            )
        raise (Exception("not implemented"))
        return []

    def __init__(self, config: TestConfig):
        self.repo = None
        self.config = config
        if config.mode == DetectionTestingMode.selected:
            if config.detections_list is None or len(config.detections_list) < 1:
                raise (
                    Exception(
                        f"Error: detection mode [{config.mode}] REQUIRES that [{config.detections_list}] contains 1 or more detections, but the value is [{config.detections_list}]"
                    )
                )
            else:
                # Ensure that all of the detections exist
                missing_files = [
                    detection
                    for detection in config.detections_list
                    if not pathlib.Path(detection).is_file()
                ]
                if len(missing_files) > 0:
                    missing_string = "\n\t - ".join(missing_files)
                    raise (
                        Exception(
                            f"Error: The following detection(s) test do not exist:\n\t - {missing_files}"
                        )
                    )
                else:
                    return

        elif config.mode == DetectionTestingMode.changes:
            # Changes is ONLY possible if the app is version controlled
            # in a github repo.  Ensure that this is the case and, if not
            # raise an exception
            try:
                repo = git.Repo(config.repo_path)
            except Exception as e:
                raise (
                    Exception(
                        f"Error: detection mode [{config.mode}] REQUIRES that [{config.repo_path}] is a git repository, but it is not."
                    )
                )
            if config.main_branch == config.test_branch:
                raise (
                    Exception(
                        f"Error: test_branch [{config.test_branch}] is the same as the main_branch [{config.main_branch}]. When using mode [{config.mode}], these two branches MUST be different."
                    )
                )

            # Ensure that the test branch is checked out
            if self.repo.active_branch.name != config.test_branch:
                raise (
                    Exception(
                        f"Error: detection mode [{config.mode}] REQUIRES that the test_branch [{config.test_branch}] be checked out at the beginning of the test, but it is not."
                    )
                )

            # Ensure that the base branch exists

            if Utils.validate_git_branch_name(
                config.repo_path, "NO_URL", config.main_branch
            ):
                return

        elif config.mode == DetectionTestingMode.all:
            return
        else:
            raise (
                Exception(
                    f"Unsupported detection testing mode [{config.mode}].  Supported detection testing modes are [{DetectionTestingMode._member_names_}]"
                )
            )

        pass

    def __init2__(self, config: TestConfig):

        self.repo = git.Repo(config.repo_path)

        if self.repo.active_branch.name != config.test_branch:
            print(
                f"Error - test_branch is '{config.test_branch}', but the current active branch in '{config.repo_path}' is '{self.repo.active_branch}'. Checking out the branch you specified..."
            )
            self.repo.git.checkout(config.test_branch)

        self.config = config

    def clone_project(self, url, project, branch):
        LOGGER.info(f"Clone Security Content Project")
        repo_obj = git.Repo.clone_from(url, project, branch=branch)
        return repo_obj

    def get_detections_to_test(
        self,
        config: TestConfig,
        director: DirectorOutputDto,
        ignore_experimental: bool = True,
        ignore_deprecated: bool = True,
        ignore_ssa: bool = True,
        allowed_types: list[str] = ["Anomaly", "Hunting", "TTP"],
    ) -> list[Detection]:

        print(f"Total detections found: {len(director.detections)}")

        if ignore_experimental:
            director.detections = [
                d for d in director.detections if not (d.experimental == True)
            ]
        if ignore_deprecated:
            director.detections = [
                d for d in director.detections if not (d.deprecated == True)
            ]
        if ignore_ssa:
            director.detections = [
                d
                for d in director.detections
                if not pathlib.Path(d.file_path).name.startswith(SSA_PREFIX)
            ]

        print(
            f"Total detections loaded after removal of experimental, deprecated, and ssa: {len(director.detections)}"
        )

        # Downselect to only the types we want to test. For example, this will by default remove the Correlation type
        director.detections = [
            d for d in director.detections if d.type in allowed_types
        ]

        if config.mode == DetectionTestingMode.changes:

            untracked_files, changed_files = self.get_all_modified_content(
                director.detections
            )
            newline_tab = "\n\t"
            print(
                f"Found the following untracked files:\n\t{newline_tab.join([f.file_path for f in untracked_files])}"
            )
            print(
                f"Found the following modified  files:\n\t{newline_tab.join([f.file_path for f in changed_files])}"
            )

            director.detections = untracked_files + changed_files

        elif config.mode == DetectionTestingMode.all:
            # Don't need to do anything, we don't need to remove it from the list
            pass
        elif config.mode == DetectionTestingMode.selected:
            if config.detections_list is None:
                # We should never get here because validation should catch it.  Adding this test to avoid
                # type warning
                raise (
                    Exception(
                        f"Detection Testing mode is {config.mode}. but Detections List was {config.detections_list}"
                    )
                )

            selected_set = set(
                os.path.join(config.repo_path, d) for d in config.detections_list
            )
            all_detections_set = set([d.file_path for d in director.detections])
            difference = selected_set - all_detections_set
            if len(difference) > 0:
                newline = "\n * "
                print(list(all_detections_set)[:10])
                raise (
                    Exception(
                        f"The detections in the detections_list do not exist:{newline}{newline.join(difference)}"
                    )
                )

            # All the detections exist, so find them an update the objects to reflect them
            director.detections = [
                d for d in director.detections if d.file_path in selected_set
            ]
        else:
            raise (
                Exception(
                    f"Unsupported mode {config.mode}.  Supported modes are {DetectionTestingMode._member_names_}"
                )
            )

        print(f"Finally the number is: {len(director.detections)}")

        if config.mode != DetectionTestingMode.selected:
            # If the user has selected specific detections to run, then
            # run those in that specific order.  Otherwise, shuffle the order.
            # This is particulary important when doing a mock because, for example,
            # we don't want one container to get a group of cloud detections which may,
            # on average, run for longer than the group of endpoint detections on
            # another container
            random.shuffle(director.detections)

        return director.detections

    def get_all_modified_content(
        self,
        detections: list[Detection],
        paths: list[pathlib.Path] = [
            pathlib.Path("detections/"),
            pathlib.Path("tests/"),
        ],
    ) -> Tuple[list[Detection], list[Detection]]:
        # Note that at present, we only search in the 'detections' and 'tests' folders.  In the future, we could search in all
        # folders, for example to evaluate any content affected by a macro or playbook change.

        try:

            # Because we have not passed -all as a kwarg, we will have a MAX of one commit returned:
            # https://gitpython.readthedocs.io/en/stable/reference.html?highlight=merge_base#git.repo.base.Repo.merge_base
            base_commits = self.repo.merge_base(
                self.config.main_branch, self.config.test_branch
            )
            if len(base_commits) == 0:
                raise (
                    Exception(
                        f"Error, main branch '{self.config.main_branch}' and test branch '{self.config.test_branch}' do not share a common ancestor"
                    )
                )
            base_commit = base_commits[0]
            if base_commit is None:
                raise (
                    Exception(
                        f"Error, main branch '{self.config.main_branch}' and test branch '{self.config.test_branch}' common ancestor commit was 'None'"
                    )
                )

            all_changes = base_commit.diff(
                self.config.test_branch, paths=[str(path) for path in paths]
            )

            # distill changed files down to the paths of added or modified files
            all_changes_paths = [
                os.path.join(self.config.repo_path, change.b_path)
                for change in all_changes
                if change.change_type in ["M", "A"]
            ]

            # untracked_files = [detection for detection in detections if detection.file_path in self.repo.untracked_files or detection.test.file_path in self.repo.untracked_files]
            # changed_files = [detection for detection in detections if detection.file_path in all_changes or detection.test.file_path in all_changes]

            # we must do this call BEFORE the list comprehension because otherwise untracked files are enumerated on each
            # iteration through the list and it is EXTREMELY slow
            repo_untracked_files = self.repo.untracked_files

            untracked_files = [
                detection
                for detection in detections
                if detection.file_path in repo_untracked_files
                or detection.test.file_path in repo_untracked_files
            ]
            changed_files = [
                detection
                for detection in detections
                if detection.file_path in all_changes_paths
                or detection.test.file_path in all_changes_paths
            ]
        except Exception as e:
            print(f"Error enumerating modified content: {str(e)}")
            sys.exit(1)

        return untracked_files, changed_files
