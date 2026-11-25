import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property var settingsViewModel

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        Text {
            text: "Companion settings"
            color: "#392820"
            font.pixelSize: 18
            font.weight: Font.DemiBold
        }

        CheckBox {
            objectName: "settingsWorkerEnabledCheckBox"
            text: "Enable worker in merged configuration"
            checked: root.settingsViewModel
                     ? root.settingsViewModel.worker_enabled : false
            enabled: root.settingsViewModel
                     && (root.settingsViewModel.can_enable_worker || checked)
            Accessible.name: text
            onToggled: root.settingsViewModel.set_worker_enabled(checked)
        }

        Text {
            objectName: "settingsWorkerGateText"
            visible: root.settingsViewModel
                     && !root.settingsViewModel.can_enable_worker
            text: "Host mode is accepted risk, not OS isolation. Worker execution stays locked until OpenCode command mediation is proven through the companion."
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Text {
            text: "The OpenCode model ID is local to this companion and is never included in the config PR."
            textFormat: Text.PlainText
            color: "#66564a"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        TextField {
            id: modelIdInput
            objectName: "settingsModelIdInput"
            Layout.fillWidth: true
            text: root.settingsViewModel
                  ? root.settingsViewModel.opencode_model_id : ""
            Accessible.name: "OpenCode model ID"
            placeholderText: "Select an exact OpenCode model ID"
            onTextEdited: root.settingsViewModel.set_opencode_model_id(text)
        }

        ComboBox {
            id: modelPicker
            objectName: "settingsModelPicker"
            Layout.fillWidth: true
            model: root.settingsViewModel
                   ? root.settingsViewModel.available_models : []
            currentIndex: root.settingsViewModel
                          ? root.settingsViewModel.available_models.indexOf(
                                root.settingsViewModel.opencode_model_id) : -1
            Accessible.name: "OpenCode model ladder"
            onActivated: function(index) {
                root.settingsViewModel.use_model(
                    root.settingsViewModel.available_models[index])
            }
        }

        Text {
            objectName: "settingsModelSuggestion"
            visible: root.settingsViewModel
                     && root.settingsViewModel.next_model_suggestion !== ""
            text: {
                if (!root.settingsViewModel
                        || root.settingsViewModel.next_model_suggestion === "")
                    return ""
                return "Next model on the ladder: "
                    + root.settingsViewModel.next_model_suggestion
                    + " (switch only after the owner confirms quota exhaustion)"
            }
            textFormat: Text.PlainText
            color: "#66564a"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Text {
            text: "Polling: adaptive"
            textFormat: Text.PlainText
            color: "#66564a"
        }

        ComboBox {
            id: motionPreference
            objectName: "settingsMotionPreference"
            Layout.fillWidth: true
            model: ["System default", "Reduced motion", "Disabled motion"]
            currentIndex: {
                if (!root.settingsViewModel
                        || root.settingsViewModel.motion_preference === "")
                    return 0
                return root.settingsViewModel.motion_preference === "reduced" ? 1 : 2
            }
            Accessible.name: "Motion preference"
            onActivated: function(index) {
                var preferences = ["", "reduced", "disabled"]
                root.settingsViewModel.set_motion_preference(preferences[index])
            }
        }

        Button {
            objectName: "settingsPublishButton"
            text: "Validate and create/update config PR"
            Accessible.name: "Publish settings as a config pull request"
            onClicked: root.settingsViewModel.publish()
        }

        Text {
            objectName: "settingsConfigPrStatus"
            text: {
                if (!root.settingsViewModel)
                    return "Configuration publisher unavailable"
                if (root.settingsViewModel.config_pr_url === "")
                    return "Config PR: " + root.settingsViewModel.config_pr_state
                return "Config PR: " + root.settingsViewModel.config_pr_state
                    + " - " + root.settingsViewModel.config_pr_merge_status
                    + " - " + root.settingsViewModel.config_pr_url
            }
            textFormat: Text.PlainText
            color: "#392820"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Text {
            visible: root.settingsViewModel
                     && root.settingsViewModel.config_pr_validation_status !== "valid"
            text: root.settingsViewModel
                  ? "Validation: " + root.settingsViewModel.config_pr_validation_status : ""
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Text {
            visible: root.settingsViewModel
                     && root.settingsViewModel.validation_message !== ""
            text: root.settingsViewModel
                  ? root.settingsViewModel.validation_message : ""
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Text {
            visible: root.settingsViewModel
                     && root.settingsViewModel.publication_error !== ""
            text: root.settingsViewModel
                  ? root.settingsViewModel.publication_error : ""
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }
}
