import QtQuick
import QtQuick.Controls

Label {
    id: root

    property string accessibleLabel: text
    color: "#41665d"
    font.pixelSize: 20
    Accessible.name: accessibleLabel
    Accessible.role: Accessible.StaticText
}
