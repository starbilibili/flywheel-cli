# Resources hide their internal layout

Flywheel treats Dataset, Model, Evaluation Config, and Script as complete registered resources. A Task Spec selects each resource only by reference; typed internal manifests and resource adapters resolve files, commands, and runtime bindings. This keeps resource layout out of the user contract and lets registration normalize heterogeneous source material without changing evaluation submissions.
