# Check the installed Waypost version

Run `seo-manager --version` to print the package version and exit successfully.
`python -m seo_manager.cli --version` reports the same version when invoked
with the Python interpreter from that installation. The output has the form
`seo-manager X.Y.Z`; the package and command retain their `seo-manager` name.

No subcommand, brand configuration, database or credentials are required.
This command does not initialize a database or invoke a report or scheduling
action. Running the CLI without a subcommand or an informational option still
returns a usage error.

The version identifies the installed package's declared release; it does not
identify an untagged commit or prove that a deployment has picked up new code.
Use the consumer's commit pin and installation provenance for those checks.
