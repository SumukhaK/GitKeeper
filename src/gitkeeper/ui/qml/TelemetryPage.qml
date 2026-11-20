import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property var telemetryViewModel

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        Text {
            text: "Local operational history"
            color: "#392820"
            font.pixelSize: 18
            font.weight: Font.DemiBold
        }

        RowLayout {
            Layout.fillWidth: true

            Text {
                text: "Retention (days)"
                color: "#66564a"
                Accessible.name: text
            }

            SpinBox {
                id: retentionDays
                objectName: "telemetryRetentionDays"
                from: 0
                to: 90
                value: root.telemetryViewModel
                       ? root.telemetryViewModel.retention_days : 90
                Accessible.name: "Telemetry retention in days, maximum 90"
            }

            Button {
                objectName: "telemetrySaveRetentionButton"
                text: "Save"
                Accessible.name: "Save telemetry retention"
                onClicked: root.telemetryViewModel
                           && root.telemetryViewModel.set_retention_days(retentionDays.value)
            }
        }

        Text {
            visible: root.telemetryViewModel
                     && root.telemetryViewModel.retention_error !== ""
            text: root.telemetryViewModel
                  ? root.telemetryViewModel.retention_error : ""
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Text {
            objectName: "telemetryEventsText"
            Layout.fillWidth: true
            Layout.fillHeight: true
            text: {
                if (!root.telemetryViewModel)
                    return "Telemetry service unavailable."
                if (root.telemetryViewModel.events.length === 0)
                    return root.telemetryViewModel.error_message !== ""
                            ? root.telemetryViewModel.error_message
                            : "No telemetry events are retained."
                return root.telemetryViewModel.events.map(function(event) {
                    var duration = event.duration_ms === null
                            ? "duration unavailable" : event.duration_ms + " ms"
                    return event.timestamp + " - " + event.repository
                        + " PR #" + event.pr_number + " @" + event.head_sha
                        + " - " + event.from_state + " -> " + event.to_state
                        + " - attempt " + event.attempt_count
                        + " - " + event.outcome + " - " + duration
                        + " - model " + (event.model_id || "not recorded")
                        + " - error " + (event.error_category || "none")
                }).join("\n")
            }
            textFormat: Text.PlainText
            color: "#392820"
            font.pixelSize: 13
            wrapMode: Text.Wrap
            Accessible.name: "Operational telemetry history"
        }

        Text {
            visible: root.telemetryViewModel
                     && root.telemetryViewModel.error_message !== ""
                     && root.telemetryViewModel.events.length > 0
            text: root.telemetryViewModel ? root.telemetryViewModel.error_message : ""
            textFormat: Text.PlainText
            color: "#8b382b"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }
}
