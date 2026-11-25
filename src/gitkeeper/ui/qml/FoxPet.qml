import QtQuick
import QtQuick.Controls

Button {
    id: root

    objectName: "foxPetButton"
    width: 104
    height: 104
    text: ""
    activeFocusOnTab: true
    focusPolicy: Qt.StrongFocus
    property string accessibleLabel: "Open GitKeeper handscroll"
    property string motionPreference: "reduced"
    Accessible.name: accessibleLabel
    Accessible.description: "Original nine-tailed fox-spirit companion"
    Accessible.role: Accessible.Button

    background: Rectangle {
        radius: 12
        color: root.down ? "#ddc9aa" : "#eadfc9"
        border.color: "#8e684d"
        border.width: 1
    }

    contentItem: Item {
        Repeater {
            model: 9

            delegate: Rectangle {
                required property int index
                width: 9
                height: 38
                radius: width / 2
                x: parent.width / 2 - width / 2 + (index - 4) * 6
                y: 10 + Math.abs(index - 4)
                rotation: (index - 4) * 7
                transformOrigin: Item.Bottom
                color: index % 2 === 0 ? "#a94e37" : "#c56a45"
                border.color: "#75412f"
                border.width: 1
            }
        }

        Rectangle {
            width: 44
            height: 38
            radius: 18
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 15
            color: "#f7e8ce"
            border.color: "#75412f"
            border.width: 1

            Rectangle {
                width: 9
                height: 16
                radius: 3
                x: 2
                y: -7
                rotation: -24
                color: "#a94e37"
            }

            Rectangle {
                width: 9
                height: 16
                radius: 3
                anchors.right: parent.right
                anchors.rightMargin: 2
                y: -7
                rotation: 24
                color: "#a94e37"
            }

            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                y: 13
                spacing: 15

                Rectangle {
                    width: 4
                    height: 5
                    radius: 2
                    color: "#392820"
                }

                Rectangle {
                    width: 4
                    height: 5
                    radius: 2
                    color: "#392820"
                }
            }

            Rectangle {
                width: 5
                height: 4
                radius: 2
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 8
                color: "#a94e37"
            }
        }
    }
}
