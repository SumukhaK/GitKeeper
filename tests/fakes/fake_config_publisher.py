from copy import deepcopy


class FakeConfigPublisher:
    def __init__(self, pull_request):
        self.pull_request = pull_request
        self.published_settings = []
        self.statuses = {}

    def publish_settings(self, settings):
        self.published_settings.append(deepcopy(settings))
        return self.pull_request

    def get_config_pr_status(self, pr_url):
        return self.statuses.get(pr_url, self.pull_request)


class FakeConfigPRClient:
    def __init__(self, default_config, pull_request):
        self.default_config = deepcopy(default_config)
        self.pull_request = pull_request
        self.statuses = {}
        self.calls = []

    def get_default_branch_config(self, repository, path):
        self.calls.append(("get_default_branch_config", repository, path))
        return deepcopy(self.default_config)

    def create_or_update_config_pr(self, repository, branch, files, title, body):
        self.calls.append(
            (
                "create_or_update_config_pr",
                repository,
                branch,
                deepcopy(files),
                title,
                body,
            )
        )
        return self.pull_request

    def get_config_pr_status(self, pr_url):
        return self.statuses.get(pr_url, self.pull_request)
