import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    objectName: "handscrollPanel"
    width: 820
    height: 500
    radius: 8
    visible: panelVisible
    z: 10
    color: "#eee2ca"
    border.color: "#755740"
    border.width: 2
    property bool panelVisible: false
    property var statusViewModel
    property var queueViewModel
    property var settingsViewModel
    signal closeRequested()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        Rectangle {
            Layout.fillWidth: true
            height: 10
            radius: 5
            color: "#79563d"
        }

        RowLayout {
            Layout.fillWidth: true

            Text {
                Layout.fillWidth: true
                text: "GitKeeper - 開発の巻物"
                color: "#392820"
                font.pixelSize: 22
                font.weight: Font.DemiBold
            }

            Button {
                objectName: "handscrollCloseButton"
                text: "Close"
                Accessible.name: "Close handscroll"
                onClicked: root.closeRequested()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Button {
                objectName: "statusTabButton"
                text: "PR / status"
                property string accessibleLabel: "Show PR status"
                Accessible.name: accessibleLabel
                onClicked: pageStack.currentIndex = 0
            }

            Button {
                objectName: "queueTabButton"
                text: "Queue"
                property string accessibleLabel: "Show queue"
                Accessible.name: accessibleLabel
                onClicked: pageStack.currentIndex = 1
            }

            Button {
                objectName: "settingsTabButton"
                text: "Settings"
                property string accessibleLabel: "Show settings"
                Accessible.name: accessibleLabel
                onClicked: pageStack.currentIndex = 2
            }
        }

        StackLayout {
            id: pageStack
            objectName: "handscrollPages"
            Layout.fillWidth: true
            Layout.fillHeight: true

            PRStatusPage {
                statusViewModel: root.statusViewModel
                queueViewModel: root.queueViewModel
            }

            QueuePage {
                queueViewModel: root.queueViewModel
            }

            SettingsPage {
                settingsViewModel: root.settingsViewModel
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: 10
            radius: 5
            color: "#79563d"
        }
    }
}
