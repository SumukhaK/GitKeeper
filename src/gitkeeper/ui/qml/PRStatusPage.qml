import QtQuick
import QtQuick.Layouts

Item {
    id: root

    property var statusViewModel

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        Text {
            text: "Pull request"
            color: "#392820"
            font.pixelSize: 18
            font.weight: Font.DemiBold
        }

        AccessibleStatus {
            objectName: "statusStateText"
            text: {
                if (!root.statusViewModel)
                    return "Loading status"
                if (root.statusViewModel.is_stale)
                    return "Status is stale"
                if (root.statusViewModel.remote_state === "gitkeeper/worker-running")
                    return "Running"
                if (root.statusViewModel.remote_state === "gitkeeper/worker-queued")
                    return "Queued"
                if (root.statusViewModel.remote_state === "gitkeeper/worker-blocked")
                    return "Blocked"
                if (root.statusViewModel.ci_result === "passed")
                    return "Ready - no worker queued"
                return "Waiting for current check status"
            }
            color: "#41665d"
            font.pixelSize: 20
            Accessible.name: text
        }

        Text {
            objectName: "statusRepositoryText"
            text: root.statusViewModel && root.statusViewModel.repository
                    ? root.statusViewModel.repository + "  -  PR #" + root.statusViewModel.pr_number
                    : "No pull request selected"
            textFormat: Text.PlainText
            color: "#392820"
            font.pixelSize: 15
        }

        Text {
            objectName: "workerStateText"
            text: "Local worker: " + (root.statusViewModel ? root.statusViewModel.worker_state || "idle" : "idle")
            textFormat: Text.PlainText
            color: "#66564a"
            font.pixelSize: 13
        }

        Text {
            text: "Head SHA: " + (root.statusViewModel ? root.statusViewModel.current_sha || "unavailable" : "unavailable")
            textFormat: Text.PlainText
            color: "#66564a"
            font.pixelSize: 13
        }

        Text {
            objectName: "lastUpdateText"
            text: "Last update: " + (root.statusViewModel ? root.statusViewModel.last_updated || "unavailable" : "unavailable")
            textFormat: Text.PlainText
            color: "#66564a"
            font.pixelSize: 13
        }

        Text {
            text: "Eligible: " + (root.statusViewModel && root.statusViewModel.eligibility === true ? "Yes" : "No")
            textFormat: Text.PlainText
            color: "#66564a"
            font.pixelSize: 13
        }

        Text {
            text: "CI result: " + (root.statusViewModel ? root.statusViewModel.ci_result || "unknown" : "unknown")
            textFormat: Text.PlainText
            color: "#66564a"
            font.pixelSize: 13
        }

        Text {
            visible: root.statusViewModel && root.statusViewModel.error_message !== ""
            text: root.statusViewModel ? root.statusViewModel.error_message : ""
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }
}
