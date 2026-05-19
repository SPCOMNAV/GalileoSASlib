"""
Modal Components
----------------
Modal dialog structures for the UI.
Depends on core/.
"""

from dash import html, dcc
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc


def create_details_modal() -> dbc.Modal:
    """
    Create the block details modal structure.
    
    Returns:
        dbc.Modal component
    """
    return dbc.Modal(
        id="details-modal",
        is_open=False,
        size="xl",
        scrollable=True,
        children=[
            dbc.ModalHeader(
                dbc.ModalTitle(id="modal-title"),
                close_button=True,
            ),
            dbc.ModalBody([
                # Feedback area for save/run operations
                html.Div(id="modal-save-feedback", className="mb-3"),
                
                # Main content area (form)
                html.Div(id="modal-html"),
                
                # Plot area (for SignalCorrelation, etc.)
                html.Div(id="modal-plot-html"),
                
                # Graph container (hidden by default)
                html.Div(
                    id="modal-graph-container",
                    style={"display": "none"},
                    children=[
                        dcc.Graph(
                            id="modal-corr-plot",
                            config={"displayModeBar": True},
                        )
                    ],
                ),
            ]),
            dbc.ModalFooter([
                html.Div(id="modal-actions"),
                dbc.Button(
                    "Close",
                    id="close-details-modal",
                    color="secondary",
                    className="ms-2",
                ),
            ]),
        ],
    )


def create_json_modal() -> dbc.Modal:
    """
    Create the JSON editor modal structure.
    
    Returns:
        dbc.Modal component
    """
    return dbc.Modal(
        id="json-modal",
        is_open=False,
        size="lg",
        scrollable=True,
        children=[
            dbc.ModalHeader(
                dbc.ModalTitle(id="json-modal-title"),
                close_button=True,
            ),
            dbc.ModalBody([
                # File selector
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Load from file:"),
                        dmc.Select(
                            id="json-file-select",
                            data=[],
                            value=None,
                            placeholder="Select a config file...",
                            clearable=True,
                        ),
                    ], width=8),
                    dbc.Col([
                        dbc.Button(
                            "Load",
                            id="load-json-from-file",
                            color="info",
                            className="mt-4",
                        ),
                    ], width=4),
                ], className="mb-3"),
                
                # JSON editor
                dmc.JsonInput(
                    id="json-editor-dmc",
                    label="Configuration JSON",
                    placeholder="{}",
                    formatOnBlur=True,
                    autosize=True,
                    minRows=10,
                    maxRows=25,
                    validationError="Invalid JSON",
                ),
                
                # Alert for feedback
                dmc.Alert(
                    id="json-save-alert",
                    hide=True,
                    duration=4000,
                    title="",
                    children="",
                ),
            ]),
            dbc.ModalFooter([
                dbc.Button("Save", id="save-json", color="success", className="me-2"),
                dbc.Button("Close", id="close-json", color="secondary"),
            ]),
        ],
    )


def create_wait_modal() -> dbc.Modal:
    """
    Create the wait/loading modal structure.
    
    Returns:
        dbc.Modal component
    """
    return dbc.Modal(
        id="wait-modal",
        is_open=False,
        centered=True,
        backdrop="static",
        keyboard=False,
        children=[
            dbc.ModalBody([
                html.Div([
                    dbc.Spinner(color="primary", size="lg"),
                    html.P(id="wait-modal-message", className="mt-3 mb-0"),
                ], className="text-center py-4"),
            ]),
        ],
    )


def create_ntp_selector_modal() -> dbc.Modal:
    """
    Create the NTP server selection popup modal.
    
    Displays 5 top public NTP servers from:
    https://gist.github.com/mutin-sa/eea1c396b1e610a2da1e5550d94b0453
    
    Returns:
        dbc.Modal component
    """
    # Top 5 NTP servers
    ntp_servers = [
        {
            "name": "Google Public NTP",
            "address": "time.google.com",
            "description": "Google's public NTP service, highly reliable with leap-smear support",
            "icon": "🌐",
        },
        {
            "name": "Cloudflare NTP",
            "address": "time.cloudflare.com",
            "description": "Cloudflare's anycast NTP service with low latency worldwide",
            "icon": "☁️",
        },
        {
            "name": "Microsoft NTP",
            "address": "time.windows.com",
            "description": "Microsoft's public time server, default for Windows systems",
            "icon": "🪟",
        },
        {
            "name": "Apple NTP",
            "address": "time.apple.com",
            "description": "Apple's public time server, default for macOS/iOS devices",
            "icon": "🍎",
        },
        {
            "name": "NIST Internet Time",
            "address": "time.nist.gov",
            "description": "US National Institute of Standards and Technology time server",
            "icon": "🇺🇸",
        },
    ]
    
    server_buttons = []
    for server in ntp_servers:
        server_buttons.append(
            html.Div(
                dbc.Card([
                    dbc.CardBody([
                        dbc.Row([
                            dbc.Col([
                                html.Span(server["icon"], style={"fontSize": "2rem"}),
                            ], width=2, className="d-flex align-items-center justify-content-center"),
                            dbc.Col([
                                html.H5(server["name"], className="mb-1"),
                                html.Code(server["address"], style={"fontSize": "0.9rem"}),
                                html.P(server["description"], className="text-muted small mb-0 mt-1"),
                            ], width=10),
                        ]),
                    ], className="p-2"),
                ], 
                className="mb-2 ntp-server-card",
                style={"cursor": "pointer"},
                ),
                id={"type": "ntp-server-card", "address": server["address"]},
                n_clicks=0,
            )
        )
    
    return dbc.Modal(
        id="ntp-selector-modal",
        is_open=False,
        centered=True,
        size="lg",
        children=[
            dbc.ModalHeader(
                dbc.ModalTitle([
                    html.I(className="fas fa-clock me-2"),
                    "Select NTP Server",
                ]),
                close_button=True,
            ),
            dbc.ModalBody([
                html.P(
                    "Select one of the top public NTP servers below for time synchronization:",
                    className="text-muted mb-3"
                ),
                html.Div(
                    server_buttons,
                    id="ntp-server-list",
                ),
                # Hidden store for selected server
                dcc.Store(id="selected-ntp-server-store", data=None),
            ]),
            dbc.ModalFooter([
                dbc.Button("Cancel", id="close-ntp-selector", color="secondary"),
            ]),
        ],
    )


def create_schedule_picker_modal() -> dbc.Modal:
    """
    Create a modal with a calendar/datetime picker for scheduled time selection.
    
    The picker allows selecting a date and time, which gets converted to DOY HH:MM:SS format.
    Also includes option to select "Now" for immediate recording.
    
    Note: All times are in UTC (required for GNSS/Galileo system time).
    
    Returns:
        dbc.Modal component
    """
    from datetime import datetime, timezone
    
    now_utc = datetime.now(timezone.utc)
    
    return dbc.Modal(
        id="schedule-picker-modal",
        is_open=False,
        centered=True,
        size="md",
        children=[
            dbc.ModalHeader(
                dbc.ModalTitle([
                    html.I(className="fas fa-calendar-alt me-2"),
                    "Schedule Recording Time (UTC)",
                ]),
                close_button=True,
            ),
            dbc.ModalBody([
                html.P(
                    "Select when to start the recording/authentication (all times in UTC):",
                    className="text-muted mb-3"
                ),
                
                # "Now" button for immediate recording
                dbc.Button(
                    [
                        html.I(className="fas fa-play-circle me-2"),
                        "Record Now (Immediate)",
                    ],
                    id="schedule-now-btn",
                    color="success",
                    className="w-100 mb-4",
                    size="lg",
                ),
                
                html.Hr(),
                
                html.P(
                    "Or schedule for a specific date and time (UTC):",
                    className="text-muted mb-3"
                ),
                
                # Date picker
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Date (UTC)", className="fw-bold"),
                        dmc.DatePicker(
                            id="schedule-date-picker",
                            minDate=now_utc.date().isoformat(),
                            value=now_utc.date().isoformat(),
                            style={"width": "100%"},
                        ),
                    ], width=6),
                    dbc.Col([
                        dbc.Label("Time (UTC)", className="fw-bold"),
                        dmc.TimeInput(
                            id="schedule-time-picker",
                            value=now_utc.strftime("%H:%M:%S"),
                            style={"width": "100%"},
                        ),
                    ], width=6),
                ], className="mb-3"),
                
                # Preview of DOY format
                html.Div([
                    dbc.Label("DOY Format Preview (UTC):", className="fw-bold text-muted"),
                    html.Div(
                        id="schedule-doy-preview",
                        className="p-2 bg-light rounded font-monospace",
                        style={"fontSize": "1.1em"},
                    ),
                ], className="mt-3"),
                
                # Store for the selected datetime
                dcc.Store(id="schedule-datetime-store", data=None),
            ]),
            dbc.ModalFooter([
                dbc.Button("Cancel", id="close-schedule-picker", color="secondary"),
                dbc.Button(
                    [html.I(className="fas fa-check me-2"), "Apply Schedule"],
                    id="apply-schedule-btn",
                    color="primary",
                ),
            ]),
        ],
    )
