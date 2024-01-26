from __future__ import annotations
from contentctl.input.yml_reader import YmlReader
from pydantic import BaseModel, model_validator, ConfigDict, FilePath, UUID4
from uuid import UUID
from typing import List, Optional, Dict, Union
import pathlib
# We should determine if we want to use StrEnum, which is only present in Python3.11+
# Alternatively, we can use
# class SupportedPlatform(str, enum.Enum):        
# or install the StrEnum library from pip

from enum import StrEnum, auto


class SupportedPlatform(StrEnum):        
    windows = auto()
    linux = auto()
    macos = auto()
    containers = auto()
    # Because the following fields contain special characters 
    # (which cannot be field names) we must specifiy them manually
    google_workspace = "google-workspace"
    iaas_gcp = "iaas:gcp"
    iaas_azure = "iaas:azure"
    iaas_aws = "iaas:aws"
    azure_ad = "azure-ad"
    office_365 = "office-365"
    


class InputArgumentType(StrEnum):
    string = auto()
    path = auto()
    url = auto()
    integer = auto()
    float = auto()
    # Cannot use auto() since the case sensitivity is important
    # These should likely be converted in the ART repo to use the same case
    # As the defined types above
    String = "String"
    Path = "Path"
    Url = "Url"

class AtomicExecutor(BaseModel):
    name: str
    elevation_required: Optional[bool] = False #Appears to be optional
    command: Optional[str] = None
    steps: Optional[str] = None
    cleanup_command: Optional[str] = None

    @model_validator(mode='after')
    def ensure_mutually_exclusive_fields(self)->AtomicExecutor:
        if self.command is not None and self.steps is not None:
            raise ValueError("command and steps cannot both be defined in the executor section.  Exactly one must be defined.")
        elif self.command is None and self.steps is None:
            raise ValueError("Neither command nor steps were defined in the executor section.  Exactly one must be defined.")
        return self
    


class InputArgument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str
    type: InputArgumentType
    default: Union[str,int,float,None] = None


class DependencyExecutorType(StrEnum):
    powershell = auto()
    sh = auto()
    bash = auto()
    command_prompt = auto()

class AtomicDependency(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str
    prereq_command: str
    get_prereq_command: str

class AtomicTest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    auto_generated_guid: UUID
    description: str
    supported_platforms: List[SupportedPlatform]
    executor: AtomicExecutor
    input_arguments: Optional[Dict[str,InputArgument]] = None
    dependencies: Optional[List[AtomicDependency]] = None
    dependency_executor_name: Optional[DependencyExecutorType] = None


    @classmethod
    def getAtomicByAtomicGuid(cls, guid: UUID, all_atomics:List[AtomicTest])->AtomicTest:
        matching_atomics = [atomic for atomic in all_atomics if atomic.auto_generated_guid == guid]
        if len(matching_atomics) == 0:
            raise ValueError(f"Unable to find atomic_guid {guid} in {len(all_atomics)} atomic_tests from ART Repo")
        elif len(matching_atomics) > 1:
            raise ValueError(f"Found {len(matching_atomics)} matching tests for atomic_guid {guid} in {len(all_atomics)} atomic_tests from ART Repo")

        return matching_atomics[0]
    
    @classmethod
    def parseArtRepo(cls, repo_path:pathlib.Path=pathlib.Path("atomic-red-team"))->List[AtomicFile]:
        if not repo_path.is_dir():
            raise ValueError(f"Atomic Red Team repo does NOT exist at {repo_path.absolute()}. You can check it out with 'git clone https://github.com/redcanaryco/atomic-red-team")
        atomics_path = repo_path/"atomics"
        if not atomics_path.is_dir():
            raise ValueError(f"Atomic Red Team repo exists at {repo_path.absolute}, but atomics directory does NOT exist at {atomics_path.absolute()}. Was it deleted or renamed?")
        
        print("Found the Atomic Red Team Repo! We will validate the presence of any atomic_guid fields referenced in your detections.")

        atomic_files:List[AtomicFile] = []
        exceptions:List[Exception] = []
        for obj_path in atomics_path.glob("**/T*.yaml"):
            try:
                atomic_files.append(cls.constructAtomicFile(obj_path))
            except Exception as e:
                exceptions.append(e)
        if len(exceptions) > 0:
            exceptions_string = '\n - '.join([str(e) for e in exceptions])
            raise ValueError(f"The following {len(exceptions)} were generated when parsing the Atomic Red Team Repo:\n - {exceptions_string}")
        
        return atomic_files
    
    @classmethod
    def constructAtomicFile(cls, file_path:pathlib.Path)->AtomicFile:
        yml_dict = YmlReader.load_file(file_path) 
        atomic_file = AtomicFile.model_validate(yml_dict)
        return atomic_file
    
    @classmethod
    def getAtomicTestsFromArtRepo(cls, repo_path:pathlib.Path=pathlib.Path("atomic-red-team"))->List[AtomicTest]:
        atomic_files = cls.getAtomicFilesFromArtRepo(repo_path)
        atomic_tests:List[AtomicTest] = []
        for atomic_file in atomic_files:
            atomic_tests.extend(atomic_file.atomic_tests)
        print(f"Found {len(atomic_tests)} Atomic Simulations in the Atomic Red Team Repo")
        return atomic_tests

    
    @classmethod
    def getAtomicFilesFromArtRepo(cls, repo_path:pathlib.Path=pathlib.Path("atomic-red-team"))->List[AtomicFile]:
        return cls.parseArtRepo(repo_path)

    
    



class AtomicFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file_path: FilePath
    attack_technique: str
    display_name: str
    atomic_tests: List[AtomicTest]




# ATOMICS_PATH = pathlib.Path("./atomics")
# atomic_objects = []
# atomic_simulations = []
# for obj_path in ATOMICS_PATH.glob("**/T*.yaml"):
#     try:
#         with open(obj_path, 'r', encoding="utf-8") as obj_handle:
#             obj_data = yaml.load(obj_handle, Loader=yaml.CSafeLoader)
#             atomic_obj = AtomicFile.model_validate(obj_data)
#     except Exception as e:
#         print(f"Error parsing object at path {obj_path}: {str(e)}")
#         print(f"We have successfully parsed {len(atomic_objects)}, however!")
#         sys.exit(1)

#     print(f"Successfully parsed {obj_path}!")
#     atomic_objects.append(atomic_obj)
#     atomic_simulations += atomic_obj.atomic_tests

# print(f"Successfully parsed all {len(atomic_objects)} files!")
# print(f"Successfully parsed all {len(atomic_simulations)} simulations!")
    

        
    