import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property var queueViewModel

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        Text {
            text: "Local repair queue"
            color: "#392820"
            font.pixelSize: 18
            font.weight: Font.DemiBold
        }

        RowLayout {
            Layout.fillWidth: true

            Button {
                objectName: "queuePauseResumeButton"
                text: root.queueViewModel && root.queueViewModel.is_paused
                      ? "Resume" : "Pause"
                enabled: root.queueViewModel
                         && root.queueViewModel.controls_enabled
                Accessible.name: root.queueViewModel
                                 && root.queueViewModel.is_paused
                                 ? "Resume local worker queue"
                                 : "Pause local worker queue"
                onClicked: {
                    if (root.queueViewModel.is_paused)
                        root.queueViewModel.resume()
                    else
                        root.queueViewModel.pause()
                }
            }

            Text {
                objectName: "queuePauseStateText"
                text: !root.queueViewModel || root.queueViewModel.is_stale
                      ? "Queue status unavailable"
                      : root.queueViewModel.is_paused
                        ? "Queue paused" : "Queue active"
                color: "#66564a"
                Accessible.name: text
            }
        }

        Text {
            objectName: "queueItemsText"
            Layout.fillWidth: true
            Layout.preferredHeight: 80
            text: {
                if (!root.queueViewModel)
                    return "Loading queue"
                if (root.queueViewModel.is_stale)
                    return "Queue is stale"
                if (root.queueViewModel.items.length === 0)
                    return "No repair work is queued"
                return root.queueViewModel.items.map(function(item) {
                    return "PR #" + item.pr_number + " - " + item.reason
                        + " - attempt " + item.automatic_attempt_count
                }).join("\n")
            }
            textFormat: Text.PlainText
            color: "#392820"
            font.pixelSize: 15
            wrapMode: Text.Wrap
            Accessible.name: "Queued pull requests and repair reasons"
        }

        ColumnLayout {
            objectName: "queueControlList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 100

            Repeater {
                model: root.queueViewModel ? root.queueViewModel.items : []

                delegate: RowLayout {
                    id: queueItemRow

                    property var queueItem: modelData
                    Layout.fillWidth: true

                    Text {
                        Layout.fillWidth: true
                        text: "PR #" + queueItemRow.queueItem.pr_number
                            + " - " + queueItemRow.queueItem.state
                        textFormat: Text.PlainText
                        color: "#392820"
                        Accessible.name: text
                    }

                    Button {
                        objectName: "queueCancelButton_"
                                     + queueItemRow.queueItem.pr_number
                        visible: queueItemRow.queueItem.state === "running"
                        enabled: root.queueViewModel
                                 && root.queueViewModel.controls_enabled
                        text: "Cancel"
                        Accessible.name: "Cancel worker for PR #"
                                         + queueItemRow.queueItem.pr_number
                        onClicked: root.queueViewModel.cancel(
                                       queueItemRow.queueItem.pr_number)
                    }

                    Button {
                        objectName: "queueRetryButton_"
                                     + queueItemRow.queueItem.pr_number
                        visible: queueItemRow.queueItem.state === "blocked"
                        enabled: root.queueViewModel
                                 && root.queueViewModel.controls_enabled
                        text: "Retry"
                        Accessible.name: "Retry PR #"
                                         + queueItemRow.queueItem.pr_number
                        onClicked: root.queueViewModel.retry(
                                       queueItemRow.queueItem.pr_number)
                    }
                }
            }
        }

        Text {
            visible: root.queueViewModel && root.queueViewModel.error_message !== ""
            objectName: "queueControlErrorText"
            text: root.queueViewModel ? root.queueViewModel.error_message : ""
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }
}
