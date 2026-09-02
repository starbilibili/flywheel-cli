# Uniform resources use Wenyon Blob Registry

Flywheel stores Dataset, Model, Config, and Script Resources uniformly as Wenyon Blob Registry repos. A Resource Snapshot is one immutable Manifest Digest; a Resource Tag is only an optional user label and does not encode upload order. Repo paths are task-first: `<origin-task>/<resource-type>/<resource-id>`, where the origin task does not prevent cross-task reuse.

An updated Resource Snapshot records its parent with the reserved `flywheel_parent` Registry Ref pinned to the parent Manifest Digest. Task dependencies always pin Manifest Digests, never tags. A shared Lance table provides searchable Snapshot metadata but remains a derived index written through a single-writer interface; a successful Manifest publication remains valid when indexing is pending and can be reconciled later.

This model was chosen over storing every Resource as a Wenyon native Dataset because Registry manifests provide one content-addressed version and composition model across heterogeneous files, global Blob deduplication, and immutable refs. Native Dataset capabilities remain outside the Resource identity model.
