package com.crowdpulse.camera.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material.icons.outlined.Videocam
import androidx.compose.material.icons.outlined.Smartphone
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.crowdpulse.camera.ui.theme.*

data class CameraItem(
    val id: String,
    val label: String,
    val url: String,
    val isLive: Boolean,
    val isPhone: Boolean = false
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CameraManagerSheet(
    onDismissRequest: () -> Unit
) {
    var showAddDialog by remember { mutableStateOf(false) }

    // Dummy state for demo
    var cameras by remember {
        mutableStateOf(
            listOf(
                CameraItem("phone", "Phone Camera", "This device", isLive = true, isPhone = true),
                CameraItem("cctv1", "Gate 1 — Entry", "rtsp://192.168.1.100/ch1", isLive = true),
                CameraItem("cctv2", "Parking Lot", "rtsp://192.168.1.101/main", isLive = false)
            )
        )
    }

    ModalBottomSheet(
        onDismissRequest = onDismissRequest,
        shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp),
        containerColor = MaterialTheme.colorScheme.surface,
        dragHandle = { BottomSheetDefaults.DragHandle() },
        modifier = Modifier.fillMaxHeight(0.85f)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 20.dp)
        ) {
            // Header
            Row(
                modifier = Modifier.fillMaxWidth().padding(bottom = 16.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = "Camera Manager",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold
                )
                TextButton(onClick = { showAddDialog = true }) {
                    Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Add CCTV")
                }
            }

            // List
            LazyColumn(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                items(cameras) { cam ->
                    CameraCard(
                        item = cam,
                        onRemove = {
                            if (!cam.isPhone) {
                                cameras = cameras.filter { it.id != cam.id }
                            }
                        }
                    )
                }
                item {
                    Spacer(Modifier.height(16.dp))
                    Button(
                        onClick = { showAddDialog = true },
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = PrimaryGlow, contentColor = PrimaryAccent)
                    ) {
                        Icon(Icons.Filled.Add, contentDescription = null)
                        Spacer(Modifier.width(8.dp))
                        Text("Add CCTV Camera", fontWeight = FontWeight.SemiBold)
                    }
                    Spacer(Modifier.height(40.dp))
                }
            }
        }
    }

    if (showAddDialog) {
        AddCctvDialog(
            onDismiss = { showAddDialog = false },
            onAdd = { label, url ->
                val newCam = CameraItem(
                    id = "cctv_${System.currentTimeMillis()}",
                    label = label,
                    url = url,
                    isLive = true
                )
                cameras = cameras + newCam
                showAddDialog = false
            }
        )
    }
}

@Composable
fun CameraCard(item: CameraItem, onRemove: () -> Unit) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        color = Color(0xFFF8FAFC),
        border = BorderStroke(1.dp, BorderSubtle)
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Icon
            Box(
                modifier = Modifier
                    .size(48.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(Color.White),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    if (item.isPhone) Icons.Outlined.Smartphone else Icons.Outlined.Videocam,
                    contentDescription = null,
                    tint = TextSecondary
                )
            }
            
            Spacer(Modifier.width(16.dp))
            
            // Details
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = item.label,
                    fontWeight = FontWeight.SemiBold,
                    color = TextPrimary,
                    fontSize = 16.sp
                )
                Text(
                    text = item.url,
                    color = TextSecondary,
                    fontSize = 13.sp,
                    maxLines = 1
                )
                Spacer(Modifier.height(4.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(if (item.isLive) SuccessGreen else Color.Gray)
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = if (item.isLive) "LIVE" else "Offline",
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        color = if (item.isLive) SuccessGreen else Color.Gray
                    )
                    if (item.isPhone) {
                        Text(
                            text = " • Session stream",
                            fontSize = 12.sp,
                            color = TextSubtle
                        )
                    }
                }
            }

            // Remove button
            if (!item.isPhone) {
                IconButton(onClick = onRemove) {
                    Icon(
                        Icons.Filled.Close,
                        contentDescription = "Remove",
                        tint = TextSubtle
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AddCctvDialog(onDismiss: () -> Unit, onAdd: (String, String) -> Unit) {
    var label by remember { mutableStateOf("") }
    var url by remember { mutableStateOf("") }
    var isError by remember { mutableStateOf(false) }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp),
        containerColor = MaterialTheme.colorScheme.surface
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(24.dp)
                .padding(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Text(
                "Add CCTV Camera",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold
            )
            
            OutlinedTextField(
                value = label,
                onValueChange = { label = it },
                label = { Text("Camera Label") },
                placeholder = { Text("e.g. Main Gate, Parking Lot") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp)
            )
            
            OutlinedTextField(
                value = url,
                onValueChange = { url = it },
                label = { Text("RTSP Stream URL") },
                placeholder = { Text("rtsp://username:pass@ip:port/") },
                singleLine = true,
                isError = isError && url.isBlank(),
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp)
            )
            
            Surface(
                color = Color(0xFFFEF3C7),
                shape = RoundedCornerShape(8.dp)
            ) {
                Text(
                    text = "💡 Tip: Most CCTV cameras use\nrtsp://admin:admin@192.168.x.x:554",
                    modifier = Modifier.padding(12.dp),
                    fontSize = 13.sp,
                    color = Color(0xFF92400E)
                )
            }
            
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                TextButton(
                    onClick = onDismiss,
                    modifier = Modifier.weight(1f)
                ) {
                    Text("Cancel")
                }
                Button(
                    onClick = {
                        if (label.isNotBlank() && url.isNotBlank()) {
                            onAdd(label, url)
                        } else {
                            isError = true
                        }
                    },
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Text("Add Camera", fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}
