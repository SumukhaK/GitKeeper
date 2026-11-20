import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window

    objectName: "companionWindow"
    width: 900
    height: 620
    minimumWidth: 720
    minimumHeight: 500
    visible: true
    title: "GitKeeper"
    color: "#f4eddd"
    property bool panelVisible: false
    property string motionPreference: "reduced"

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 16

        Text {
            Layout.alignment: Qt.AlignHCenter
            text: "GitKeeper"
            color: "#392820"
            font.pixelSize: 28
            font.weight: Font.DemiBold
        }

        Text {
            Layout.alignment: Qt.AlignHCenter
            text: "Your local PR companion"
            color: "#66564a"
            font.pixelSize: 15
        }

        FoxPet {
            Layout.alignment: Qt.AlignHCenter
            motionPreference: window.motionPreference
            onClicked: window.panelVisible = true
        }

        Text {
            Layout.alignment: Qt.AlignHCenter
            text: "Select the fox to open the handscroll"
            color: "#66564a"
            font.pixelSize: 13
        }
    }

    HandscrollPanel {
        id: handscrollPanel
        anchors.centerIn: parent
        statusViewModel: appStatusViewModel
        queueViewModel: appQueueViewModel
        settingsViewModel: appSettingsViewModel
        telemetryViewModel: appTelemetryViewModel
        panelVisible: window.panelVisible
        onCloseRequested: window.panelVisible = false
    }
}
