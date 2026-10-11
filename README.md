# hexbox

## Runner relay

Private webbox Actions jobs can use one-shot self-hosted runners supplied by
public hexbox jobs. The trigger is an agentless Azure DevOps webhook pipeline;
all setup remains a user action.

See [setup, testing and diagnosis](docs/runner-relay.md).
Files: [relay workflow](.github/workflows/relay.yml),
[ADO pipeline](ado/relay-trigger.yml), [runner supervisor](scripts/relay.py).
No private job output is forwarded into public relay logs.
