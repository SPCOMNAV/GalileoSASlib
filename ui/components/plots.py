"""
Plot Components
---------------
Plotly visualization components for the UI.
Depends on core/.
"""

import base64
from pathlib import Path
from typing import Any, Optional, Dict

from dash import html, no_update
import dash_bootstrap_components as dbc

from ui.core.config import BASE_DIR


def build_almanac_skyplot(almanac_data: Optional[Dict[str, Any]]) -> Any:
    """
    Build skyplot image display for AlmanacManagement block.
    
    Args:
        almanac_data: Data from almanac execution containing skyplot_path
        
    Returns:
        Dash HTML component with skyplot image
    """
    skyplot_path = None
    skyplot_info = None
    
    if almanac_data:
        skyplot_path = almanac_data.get("skyplot_path")
        skyplot_info = almanac_data.get("skyplot", {})
    
    # Try to find latest skyplot if not in data
    if not skyplot_path:
        # Search in executions subdirs first
        executions_dir = BASE_DIR / "executions"
        if executions_dir.exists():
            # Get most recent execution
            exec_dirs = sorted([d for d in executions_dir.iterdir() if d.is_dir()], reverse=True)
            for exec_dir in exec_dirs[:5]:  # Check last 5 executions
                almanac_subdir = exec_dir / "almanac"
                if almanac_subdir.exists():
                    skyplot_files = sorted(almanac_subdir.glob("skyplot_*.png"), reverse=True)
                    if skyplot_files:
                        skyplot_path = str(skyplot_files[0])
                        break
        
        # Fallback to results/datasetION/almanac (RESULTS_DIR from utils)
        if not skyplot_path:
            almanac_dir = BASE_DIR / "results" / "datasetION" / "almanac"
            if almanac_dir.exists():
                skyplot_files = sorted(almanac_dir.glob("skyplot_*.png"), reverse=True)
                if skyplot_files:
                    skyplot_path = str(skyplot_files[0])
    
    if skyplot_path:
        skyplot_path_obj = Path(skyplot_path)
        if skyplot_path_obj.exists():
            try:
                # Read image and encode as base64
                with open(skyplot_path_obj, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                
                # Build info text
                visible_count = skyplot_info.get("visible_count", "?")
                total_count = skyplot_info.get("total_count", "?")
                target_dt = almanac_data.get("skyplot_datetime", "") if almanac_data else ""
                
                info_text = f"{visible_count}/{total_count} satellites visible"
                if target_dt:
                    info_text += f" at {target_dt[:19]}"
                
                return html.Div([
                    dbc.Alert(
                        [
                            html.I(className="fas fa-satellite me-2"),
                            f"Satellite Skyplot - {info_text}",
                        ],
                        color="info",
                        className="mb-2",
                    ),
                    html.Div(
                        html.Img(
                            src=f"data:image/png;base64,{img_data}",
                            style={
                                "maxWidth": "100%",
                                "height": "auto",
                                "border": "1px solid #dee2e6",
                                "borderRadius": "4px",
                            },
                        ),
                        style={"textAlign": "center"},
                    ),
                ])
            except Exception as e:
                return dbc.Alert(f"Error loading skyplot: {e}", color="warning")
    
    return html.Div()


def build_signal_corr_plot(signal_corr_data: Optional[Dict[str, Any]]) -> Any:
    """
    Build plot HTML for SignalCorrelation block.
    
    Args:
        signal_corr_data: Data from signal correlation execution
        
    Returns:
        Dash HTML component or no_update
    """
    plot_path = None
    
    if signal_corr_data:
        plot_path = signal_corr_data.get("plot_path")
    
    # Try to find latest plot if not in data
    if not plot_path:
        plots_dir = BASE_DIR / "results" / "datasetION" / "plots"
        if plots_dir.exists():
            plot_files = sorted(plots_dir.glob("correlation_*.html"), reverse=True)
            if plot_files:
                plot_path = str(plot_files[0])
    
    if plot_path:
        plot_path_obj = Path(plot_path)
        if plot_path_obj.exists():
            try:
                with open(plot_path_obj, "r", encoding="utf-8") as f:
                    html_content = f.read()
                
                return html.Div([
                    dbc.Alert(
                        [
                            html.I(className="fas fa-chart-line me-2"),
                            f"Correlation Plot - {plot_path_obj.name}",
                        ],
                        color="info",
                        className="mb-2",
                    ),
                    html.Iframe(
                        srcDoc=html_content,
                        style={
                            "width": "100%",
                            "height": "600px",
                            "border": "1px solid #dee2e6",
                            "borderRadius": "4px",
                        },
                    ),
                ])
            except Exception as e:
                return dbc.Alert(f"Error loading plot: {e}", color="warning")
    
    return html.Div()


def build_authenticator_plot(authenticator_data: Optional[Dict[str, Any]]) -> Any:
    """
    Build plot HTML for SatelliteAuthenticator block.
    
    Shows authentication results summary and plots.
    
    Args:
        authenticator_data: Data from authenticator execution
        
    Returns:
        Dash HTML component with results and plots
    """
    components = []
    plot_path = None
    
    # Build authentication summary from data
    if authenticator_data:
        # Check for plot_path or plot_file (both names used in different versions)
        plot_path = authenticator_data.get("plot_path") or authenticator_data.get("plot_file")
        
        # Build summary card
        position_auth = authenticator_data.get("position_authenticated", False)
        auth_count = authenticator_data.get("authenticated_count", 0)
        failed_count = authenticator_data.get("failed_count", 0)
        skipped_count = authenticator_data.get("skipped_count", 0)
        total_prns = authenticator_data.get("total_prns", 0)
        summary_text = authenticator_data.get("summary", "No summary available")
        
        # Determine color based on authentication result
        if position_auth:
            color = "success"
            icon = "fa-check-circle"
            title = "✅ POSITION AUTHENTICATED"
        elif failed_count > 0:
            color = "danger"
            icon = "fa-times-circle"
            title = "❌ AUTHENTICATION FAILED"
        else:
            color = "warning"
            icon = "fa-exclamation-triangle"
            title = "⚠️ UNABLE TO AUTHENTICATE"
        
        # Config info
        config = authenticator_data.get("config", {})
        kappa = config.get("kappa", 3.0)
        gamma_auth_m = config.get("gamma_auth_m", 0)
        sigma_auth_m = config.get("sigma_auth_m", 0)
        confidence = config.get("confidence_level_percent", 99.73)
        
        # Summary card
        summary_card = dbc.Card([
            dbc.CardHeader([
                html.I(className=f"fas {icon} me-2"),
                html.Strong(title),
            ], className=f"bg-{color} text-white"),
            dbc.CardBody([
                html.P(summary_text.replace("\\n", " | "), className="mb-2"),
                html.Hr(),
                dbc.Row([
                    dbc.Col([
                        html.Strong("Satellites:"),
                        html.Div([
                            dbc.Badge(f"✓ {auth_count} Authenticated", color="success", className="me-1"),
                            dbc.Badge(f"✗ {failed_count} Failed", color="danger", className="me-1") if failed_count > 0 else None,
                            dbc.Badge(f"○ {skipped_count} Skipped", color="secondary", className="me-1") if skipped_count > 0 else None,
                        ]),
                    ], md=4),
                    dbc.Col([
                        html.Strong("Threshold (γ_auth):"),
                        html.Div(f"{gamma_auth_m:.2f} m (κ={kappa}, {confidence:.1f}% conf.)"),
                    ], md=4),
                    dbc.Col([
                        html.Strong("Sigma (σ_auth):"),
                        html.Div(f"{sigma_auth_m:.2f} m"),
                    ], md=4),
                ]),
            ]),
        ], className="mb-3")
        components.append(summary_card)
        
        # PRN results table
        prn_results = authenticator_data.get("prn_results", [])
        if prn_results:
            table_rows = []
            for prn_res in prn_results:
                prn = prn_res.get("prn", "?")
                auth = prn_res.get("authenticated")
                status = prn_res.get("status", "")
                delta_m = prn_res.get("delta_meters", 0)
                delta_samples = prn_res.get("delta_samples", 0)
                snr_e1b = prn_res.get("snr_e1b_db", 0)
                snr_e6c = prn_res.get("snr_e6c_db", 0)
                
                if auth is True:
                    badge = dbc.Badge("✓ AUTH", color="success")
                elif auth is False:
                    badge = dbc.Badge("✗ FAIL", color="danger")
                else:
                    badge = dbc.Badge("○ SKIP", color="secondary")
                
                table_rows.append(html.Tr([
                    html.Td(f"PRN {prn}"),
                    html.Td(badge),
                    html.Td(f"{delta_samples:.3f} samp" if auth is not None else "-"),
                    html.Td(f"{delta_m:.2f} m" if auth is not None else "-"),
                    html.Td(f"{snr_e1b:.1f} / {snr_e6c:.1f}" if auth is not None else "-"),
                    html.Td(status, style={"fontSize": "0.85em"}),
                ]))
            
            results_table = dbc.Table([
                html.Thead(html.Tr([
                    html.Th("PRN"),
                    html.Th("Result"),
                    html.Th("Δ (samples)"),
                    html.Th("Δ (meters)"),
                    html.Th("SNR E1B/E6C"),
                    html.Th("Status"),
                ])),
                html.Tbody(table_rows),
            ], bordered=True, hover=True, striped=True, size="sm", className="mb-3")
            components.append(results_table)
    
    # Try to find latest plot if not in data
    if not plot_path:
        auth_results_dir = BASE_DIR / "results" / "authentication"
        if auth_results_dir.exists():
            # Search for both naming patterns: authentication_plot_*.html and acas_auth_*.html
            plot_files = sorted(auth_results_dir.glob("authentication_plot_*.html"), reverse=True)
            if not plot_files:
                plot_files = sorted(auth_results_dir.glob("acas_auth_*.html"), reverse=True)
            if plot_files:
                plot_path = str(plot_files[0])
    
    # Add plot if available
    if plot_path:
        plot_path_obj = Path(plot_path)
        if plot_path_obj.exists():
            try:
                with open(plot_path_obj, "r", encoding="utf-8") as f:
                    html_content = f.read()
                
                components.append(html.Div([
                    dbc.Alert(
                        [
                            html.I(className="fas fa-chart-bar me-2"),
                            f"Authentication Plots - {plot_path_obj.name}",
                        ],
                        color="info",
                        className="mb-2",
                    ),
                    html.Iframe(
                        srcDoc=html_content,
                        style={
                            "width": "100%",
                            "height": "800px",
                            "border": "1px solid #dee2e6",
                            "borderRadius": "4px",
                        },
                    ),
                ]))
            except Exception as e:
                components.append(dbc.Alert(f"Error loading plot: {e}", color="warning"))
    
    # E1B vs E6C peak comparison plot
    comparison_plot_path = None
    if authenticator_data:
        comparison_plot_path = authenticator_data.get("comparison_plot_file")
    
    # Try to find latest comparison plot if not in data
    if not comparison_plot_path:
        auth_results_dir = BASE_DIR / "results" / "authentication"
        if auth_results_dir.exists():
            comp_files = sorted(auth_results_dir.glob("auth_peak_comparison_*.html"), reverse=True)
            if comp_files:
                comparison_plot_path = str(comp_files[0])
    
    if comparison_plot_path:
        comp_path_obj = Path(comparison_plot_path)
        if comp_path_obj.exists():
            try:
                with open(comp_path_obj, "r", encoding="utf-8") as f:
                    comp_html = f.read()
                
                components.append(html.Div([
                    dbc.Alert(
                        [
                            html.I(className="fas fa-wave-square me-2"),
                            f"E1B vs E6C Peak Comparison - {comp_path_obj.name}",
                        ],
                        color="primary",
                        className="mb-2 mt-3",
                    ),
                    html.Iframe(
                        srcDoc=comp_html,
                        style={
                            "width": "100%",
                            "height": "750px",
                            "border": "1px solid #dee2e6",
                            "borderRadius": "4px",
                        },
                    ),
                ]))
            except Exception as e:
                components.append(dbc.Alert(f"Error loading comparison plot: {e}", color="warning"))
    
    # Return components or empty message
    if components:
        return html.Div(components)
    else:
        return dbc.Alert(
            [
                html.I(className="fas fa-info-circle me-2"),
                "No authentication results available. Run the pipeline with E1B + E6C (ECS) to see results.",
            ],
            color="info",
        )
