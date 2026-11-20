import QtQuick
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

        Text {
            objectName: "queueItemsText"
            Layout.fillWidth: true
            Layout.fillHeight: true
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

        Text {
            visible: root.queueViewModel && root.queueViewModel.error_message !== ""
            text: root.queueViewModel ? root.queueViewModel.error_message : ""
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }
}
