import pytest

from gitkeeper.policy.patch_scope import (
    FileChange,
    PatchScopePolicy,
    RemotePrState,
    ScopeReason,
    ScopeRequest,
)

STARTING_SHA = "a" * 40
MANIFEST = "app/src/main/AndroidManifest.xml"


@pytest.fixture
def roots(tmp_path):
    managed = tmp_path / "managed"
    worktree = managed / "worktrees" / "setu-pr-19"
    gitkeeper_checkout = managed / "GitKeeper"
    setu_checkout = managed / "Setu-mobile"
    for path in (worktree, gitkeeper_checkout, setu_checkout):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "managed": managed,
        "worktree": worktree,
        "active": (gitkeeper_checkout, setu_checkout),
    }


def manifest(body):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools"
    package="com.setu">
{body}
</manifest>
""".encode("utf-8")


BASE_BODY = """    <uses-permission android:name="android.permission.INTERNET" />
    <application
        android:allowBackup="false"
        android:usesCleartextTraffic="false">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <activity android:name=".SettingsActivity" android:exported="false" />
    </application>"""


def base_manifest():
    return manifest(BASE_BODY)


def changed_manifest(body):
    return manifest(body)


def manifest_change(old_content, new_content):
    return FileChange(
        path=MANIFEST,
        status="modified",
        added_lines=2,
        deleted_lines=1,
        old_mode="100644",
        new_mode="100644",
        old_content=old_content,
        new_content=new_content,
    )


def source_change():
    return FileChange(
        path="app/src/main/java/com/setu/LoginViewModel.kt",
        status="modified",
        added_lines=4,
        deleted_lines=1,
    )


def make_test_change():
    return FileChange(
        path="app/src/test/java/com/setu/LoginViewModelTest.kt",
        status="modified",
        added_lines=4,
        deleted_lines=0,
    )


def evaluate(roots, *changes, with_source=True):
    change_set = list(changes)
    if with_source:
        change_set.extend([source_change(), make_test_change()])
    request = ScopeRequest(
        starting_sha=STARTING_SHA,
        head_sha=STARTING_SHA,
        worktree_path=roots["worktree"],
        managed_root=roots["managed"],
        active_checkouts=roots["active"],
        changes=tuple(change_set),
        remote=RemotePrState(
            number=19,
            repository="SumukhaK/Setu-mobile",
            author="SumukhaK",
            head_sha=STARTING_SHA,
            is_open=True,
            is_draft=False,
        ),
    )
    return PatchScopePolicy().evaluate(request)


def codes(decision):
    return {violation.code for violation in decision.violations}


def test_label_and_description_edits_inside_existing_elements_are_allowed(roots):
    body = BASE_BODY.replace(
        '<activity android:name=".SettingsActivity" android:exported="false" />',
        '<activity\n'
        '            android:name=".SettingsActivity"\n'
        '            android:exported="false"\n'
        '            android:label="@string/settings_title" />',
    )

    decision = evaluate(roots, manifest_change(base_manifest(), changed_manifest(body)))

    assert decision.allowed is True


def test_adding_a_permission_is_rejected(roots):
    body = BASE_BODY.replace(
        '    <uses-permission android:name="android.permission.INTERNET" />',
        '    <uses-permission android:name="android.permission.INTERNET" />\n'
        '    <uses-permission android:name="android.permission.CAMERA" />',
    )

    decision = evaluate(roots, manifest_change(base_manifest(), changed_manifest(body)))

    assert codes(decision) == {ScopeReason.MANIFEST_CAPABILITY}


def test_removing_a_permission_is_rejected(roots):
    body = BASE_BODY.replace(
        '    <uses-permission android:name="android.permission.INTERNET" />\n', ""
    )

    decision = evaluate(roots, manifest_change(base_manifest(), changed_manifest(body)))

    assert ScopeReason.MANIFEST_CAPABILITY in codes(decision)


def test_declaring_a_new_component_is_rejected(roots):
    body = BASE_BODY.replace(
        "</application>",
        '    <service android:name=".SyncService" android:exported="false" />\n'
        "    </application>",
    )

    decision = evaluate(roots, manifest_change(base_manifest(), changed_manifest(body)))

    assert codes(decision) == {ScopeReason.MANIFEST_CAPABILITY}


def test_changing_android_exported_is_rejected(roots):
    body = BASE_BODY.replace(
        '<activity android:name=".SettingsActivity" android:exported="false" />',
        '<activity android:name=".SettingsActivity" android:exported="true" />',
    )

    decision = evaluate(roots, manifest_change(base_manifest(), changed_manifest(body)))

    assert codes(decision) == {ScopeReason.MANIFEST_CAPABILITY}


def test_adding_an_intent_filter_is_rejected(roots):
    body = BASE_BODY.replace(
        '<activity android:name=".SettingsActivity" android:exported="false" />',
        '<activity android:name=".SettingsActivity" android:exported="false">\n'
        "            <intent-filter>\n"
        '                <action android:name="android.intent.action.VIEW" />\n'
        "            </intent-filter>\n"
        "        </activity>",
    )

    decision = evaluate(roots, manifest_change(base_manifest(), changed_manifest(body)))

    assert codes(decision) == {ScopeReason.MANIFEST_CAPABILITY}


def test_enabling_cleartext_traffic_is_rejected(roots):
    body = BASE_BODY.replace(
        'android:usesCleartextTraffic="false"', 'android:usesCleartextTraffic="true"'
    )

    decision = evaluate(roots, manifest_change(base_manifest(), changed_manifest(body)))

    assert codes(decision) == {ScopeReason.MANIFEST_CAPABILITY}


def test_enabling_debuggable_is_rejected(roots):
    body = BASE_BODY.replace(
        'android:allowBackup="false"', 'android:allowBackup="false" android:debuggable="true"'
    )

    decision = evaluate(roots, manifest_change(base_manifest(), changed_manifest(body)))

    assert codes(decision) == {ScopeReason.MANIFEST_CAPABILITY}


def test_enabling_backup_is_rejected(roots):
    body = BASE_BODY.replace('android:allowBackup="false"', 'android:allowBackup="true"')

    decision = evaluate(roots, manifest_change(base_manifest(), changed_manifest(body)))

    assert codes(decision) == {ScopeReason.MANIFEST_CAPABILITY}


def test_an_unparseable_manifest_is_rejected(roots):
    decision = evaluate(
        roots,
        manifest_change(base_manifest(), b"<manifest><application>"),
    )

    assert codes(decision) == {ScopeReason.MANIFEST_UNREADABLE}


def test_an_added_manifest_in_a_new_module_is_rejected_because_it_is_not_allowlisted(roots):
    added = FileChange(
        path="feature/sync/AndroidManifest.xml",
        status="added",
        added_lines=12,
        deleted_lines=0,
        new_content=base_manifest(),
    )

    decision = evaluate(roots, added)

    assert ScopeReason.PATH_DENIED in codes(decision)


def test_a_manifest_only_patch_is_not_a_source_change(roots):
    body = BASE_BODY.replace(
        '<activity android:name=".SettingsActivity" android:exported="false" />',
        '<activity\n'
        '            android:name=".SettingsActivity"\n'
        '            android:exported="false"\n'
        '            android:label="@string/settings_title" />',
    )

    decision = evaluate(
        roots, manifest_change(base_manifest(), changed_manifest(body)), with_source=False
    )

    assert codes(decision) == {ScopeReason.NO_SOURCE_CHANGE}
