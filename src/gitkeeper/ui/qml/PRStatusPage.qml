import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property var statusViewModel
    property var queueViewModel

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

        Text {
            text: "Manual Claude CHANGES_REQUESTED feedback"
            color: "#392820"
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }

        Text {
            text: "Transfer feedback yourself; GitKeeper does not access Claude."
            textFormat: Text.PlainText
            color: "#66564a"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        RowLayout {
            Layout.fillWidth: true

            TextField {
                id: feedbackPrNumberInput
                objectName: "feedbackPrNumberInput"
                Layout.fillWidth: true
                text: root.statusViewModel
                      ? String(root.statusViewModel.pr_number || "") : ""
                inputMethodHints: Qt.ImhDigitsOnly
                Accessible.name: "Pull request number for review feedback"
                placeholderText: "Pull request number"
            }

            Button {
                objectName: "feedbackLoadPrButton"
                text: "Load PR"
                Accessible.name: "Load pull request for review feedback"
                onClicked: {
                    var prNumber = parseInt(feedbackPrNumberInput.text, 10)
                    if (!isNaN(prNumber) && String(prNumber) === feedbackPrNumberInput.text.trim()) {
                        root.statusViewModel.select_pull_request(prNumber)
                        feedbackHeadShaInput.text = root.statusViewModel.current_sha
                        feedbackTextInput.clear()
                        feedbackReassociateCheckBox.checked = false
                    }
                }
            }
        }

        Text {
            text: "Current head SHA: " + (root.statusViewModel ? root.statusViewModel.current_sha || "unavailable" : "unavailable")
            textFormat: Text.PlainText
            color: "#66564a"
            font.pixelSize: 12
            Layout.fillWidth: true
        }

        TextField {
            id: feedbackHeadShaInput
            objectName: "feedbackHeadShaInput"
            Layout.fillWidth: true
            text: root.statusViewModel ? root.statusViewModel.current_sha : ""
            Accessible.name: "SHA reviewed by Claude"
            placeholderText: "Head SHA reviewed by Claude"
        }

        CheckBox {
            id: feedbackReassociateCheckBox
            objectName: "feedbackReassociateCheckBox"
            text: "Re-associate with the current head SHA"
            enabled: root.statusViewModel
                     && feedbackHeadShaInput.text !== root.statusViewModel.current_sha
            Accessible.name: text
        }

        TextArea {
            id: feedbackTextInput
            objectName: "feedbackTextInput"
            Layout.fillWidth: true
            Layout.preferredHeight: 72
            wrapMode: TextArea.Wrap
            Accessible.name: "Manually transferred Claude review feedback"
            placeholderText: "Paste the CHANGES_REQUESTED feedback here"
        }

        Text {
            objectName: "feedbackPreviewText"
            text: "Preview: " + feedbackTextInput.text
            textFormat: Text.PlainText
            color: "#66564a"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Text {
            visible: root.statusViewModel && root.statusViewModel.feedback_error !== ""
            text: root.statusViewModel ? root.statusViewModel.feedback_error : ""
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Button {
            objectName: "feedbackSubmitButton"
            text: "Submit repair feedback"
            Accessible.name: "Submit manually transferred review feedback"
            enabled: root.statusViewModel
                     && feedbackTextInput.text.trim() !== ""
                     && feedbackHeadShaInput.text !== ""
            onClicked: {
                var submitted = root.statusViewModel.submit_review_feedback(
                            feedbackTextInput.text,
                            feedbackHeadShaInput.text,
                            feedbackReassociateCheckBox.checked)
                if (submitted) {
                    feedbackTextInput.clear()
                    feedbackHeadShaInput.text = root.statusViewModel.current_sha
                    feedbackReassociateCheckBox.checked = false
                }
            }
        }
    }
}
