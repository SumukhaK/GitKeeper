import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    objectName: "commandApprovalDialog"
    visible: !!root.approvalViewModel && root.approvalViewModel.pending
    z: 20
    anchors.centerIn: parent
    width: Math.min(parent ? parent.width - 48 : 640, 640)
    height: Math.min(parent ? parent.height - 48 : 460, 460)
    radius: 8
    color: "#fbf5e6"
    border.color: "#8a2f2f"
    border.width: 2
    property var approvalViewModel
    property bool animationEnabled: !!root.approvalViewModel
                                    && root.approvalViewModel.animation_enabled

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Rectangle {
                objectName: "commandApprovalPulse"
                width: 10
                height: 10
                radius: 5
                color: "#8a2f2f"
                visible: root.animationEnabled
                SequentialAnimation on opacity {
                    running: root.animationEnabled
                    loops: Animation.Infinite
                    NumberAnimation { from: 1.0; to: 0.25; duration: 700 }
                    NumberAnimation { from: 0.25; to: 1.0; duration: 700 }
                }
            }

            Text {
                objectName: "commandApprovalTitle"
                Layout.fillWidth: true
                text: "Review command before it runs"
                color: "#392820"
                font.pixelSize: 18
                font.weight: Font.DemiBold
                Accessible.name: text
            }
        }

        Text {
            objectName: "commandApprovalSubjectText"
            Layout.fillWidth: true
            text: root.approvalViewModel
                  ? root.approvalViewModel.repository + " #"
                    + root.approvalViewModel.pr_number + " - "
                    + root.approvalViewModel.purpose
                  : ""
            color: "#66564a"
            wrapMode: Text.Wrap
            Accessible.name: text
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            TextArea {
                objectName: "commandApprovalDetails"
                readOnly: true
                selectByMouse: true
                textFormat: TextEdit.PlainText
                wrapMode: TextArea.WrapAnywhere
                font.family: "Consolas"
                font.pixelSize: 12
                color: "#2b211b"
                text: root.approvalViewModel
                      ? root.approvalViewModel.request_lines.join("\n")
                      : ""
                Accessible.name: root.approvalViewModel
                                 ? root.approvalViewModel.accessibility_description
                                 : "No command is waiting for review"
                Accessible.role: Accessible.StaticText
            }
        }

        Text {
            objectName: "commandApprovalStatusText"
            Layout.fillWidth: true
            text: root.approvalViewModel
                  ? root.approvalViewModel.status_message : ""
            color: "#41665d"
            wrapMode: Text.Wrap
            Accessible.name: text
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Button {
                objectName: "commandApprovalApproveButton"
                text: root.approvalViewModel
                      ? root.approvalViewModel.approve_label : "Approve once"
                enabled: !!root.approvalViewModel
                         && root.approvalViewModel.controls_enabled
                activeFocusOnTab: true
                Accessible.name: text
                onClicked: root.approvalViewModel.approve()
            }

            Button {
                objectName: "commandApprovalDenyButton"
                text: root.approvalViewModel
                      ? root.approvalViewModel.deny_label : "Deny"
                enabled: !!root.approvalViewModel
                         && root.approvalViewModel.controls_enabled
                activeFocusOnTab: true
                Accessible.name: text
                onClicked: root.approvalViewModel.deny()
            }
        }
    }
}
