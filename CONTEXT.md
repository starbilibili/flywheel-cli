# Flywheel Tasks

Flywheel composes registered resources into reproducible tasks while keeping each resource's internal layout private from task authors. A Task may represent evaluation, training, or another supported workload.

## Language

**Resource**:
A versioned, self-describing task input consumed as one complete object. The supported resource types are Dataset, Model, Config, Script, and Task.
_Avoid_: Asset payload, entrypoint

**Resource Name**:
A user-provided, human-readable name used to identify and search one Resource. It is independent of Resource ID and Snapshot order.
_Avoid_: Task name (for ordinary resources), version name

**Resource Snapshot**:
One immutable version created by every successful Resource registration. In Wenyon Blob Registry it is identified by the Manifest Digest. Snapshot order is independent of user-assigned tags.
_Avoid_: Tag version, user version number

**Resource Tag**:
An optional user-assigned label that points to a Resource Snapshot for convenient recognition and search. A tag such as `v2` does not mean the second Snapshot and may label any Snapshot in the version history.
_Avoid_: Version, revision number

**Resource Description**:
A stable human-readable explanation of one Resource across all its Snapshots. It is supplied or generated when the Resource is created; Snapshot-specific changes do not overwrite it.
_Avoid_: Version description, Tag description

**Task Type**:
The workload category declared by `task_type`, initially `evaluation` or `training`. It selects task-specific validation and execution behavior without changing the common Task lifecycle.
_Avoid_: Evaluation mode, training mode

**Task Spec**:
The user-authored `config.yaml` that declares a Task Type, selects resources, and provides run-level options.
_Avoid_: Evaluation config, training config, run config

**Config**:
A registered Resource containing Task-Type-specific execution settings, such as evaluation scoring or training optimization parameters.
_Avoid_: Evaluation Config as the general category

**Effective Task Config**:
The immutable, credential-free configuration produced after Flywheel resolves the Task Spec and its Resources. A Script consumes this object.
_Avoid_: Effective Run Config, merged config

**Script**:
A registered executable Resource that consumes an Effective Task Config and produces a Task Result according to a declared protocol. Its launch mechanism belongs to the Resource contract and is never selected by the Task author.
_Avoid_: Evaluator entrypoint, harness path

**Task Request Bundle Snapshot**:
The immutable task definition created after all Resource references have been resolved and frozen. It records `task_type`, the Effective Task Config, and exact Resource dependencies.
_Avoid_: Evaluation Request

**Task Run**:
One execution attempt of a Task Request, with a mutable lifecycle such as queued, running, succeeded, failed, or cancelled.
_Avoid_: Evaluation Run, Task Bundle

**Task Result Bundle Snapshot**:
The immutable output produced by a Task Run, including exact input provenance and a Ref to its Task Request Bundle Snapshot.
_Avoid_: Evaluation Result, Run Output
