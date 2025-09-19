# Add these routes to call_routes.py or create a separate debug_routes.py

from flask import jsonify, request
from datetime import datetime
import services.call_service as call_service
import services.asterisk_service as asterisk_service
from app_state import active_calls, active_calls_lock

@call_bp.route("/api/debug/call_history/<campaign_id>/<phone_number>", methods=["GET"])
@login_required
def get_call_debug_history(campaign_id, phone_number):
    """Get debug history for a specific call"""
    try:
        history = call_service.get_call_debug_history(campaign_id, phone_number)
        
        # Format timestamps for JSON
        formatted_history = []
        for entry in history:
            formatted_entry = entry.copy()
            formatted_entry['timestamp'] = entry['timestamp'].isoformat()
            formatted_history.append(formatted_entry)
        
        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "phone_number": phone_number,
            "history": formatted_history
        })
    except Exception as e:
        logging.error(f"Error getting call debug history: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@call_bp.route("/api/debug/ami_history", methods=["GET"])
@login_required
def get_ami_debug_history():
    """Get AMI debug history"""
    try:
        with asterisk_service.ami_debug_lock:
            history = asterisk_service.ami_debug_log.copy()
        
        # Format timestamps for JSON
        formatted_history = []
        for entry in history:
            formatted_entry = entry.copy()
            formatted_entry['timestamp'] = entry['timestamp'].isoformat()
            formatted_history.append(formatted_entry)
        
        return jsonify({
            "success": True,
            "history": formatted_history
        })
    except Exception as e:
        logging.error(f"Error getting AMI debug history: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@call_bp.route("/api/debug/active_calls", methods=["GET"])
@login_required
def get_active_calls_debug():
    """Get current active_calls state for debugging"""
    try:
        with active_calls_lock:
            active_calls_copy = {}
            for campaign_id, calls in active_calls.items():
                active_calls_copy[campaign_id] = {}
                for phone, call_data in calls.items():
                    call_data_copy = call_data.copy()
                    # Convert timestamp to string for JSON
                    if 'timestamp' in call_data_copy and isinstance(call_data_copy['timestamp'], datetime):
                        call_data_copy['timestamp'] = call_data_copy['timestamp'].isoformat()
                    active_calls_copy[campaign_id][phone] = call_data_copy
        
        return jsonify({
            "success": True,
            "active_calls": active_calls_copy,
            "total_campaigns": len(active_calls_copy),
            "total_calls": sum(len(calls) for calls in active_calls_copy.values())
        })
    except Exception as e:
        logging.error(f"Error getting active calls debug: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@call_bp.route("/api/debug/ami_status", methods=["GET"])
@login_required
def get_ami_status():
    """Get current AMI client status"""
    try:
        ami_status = {
            "instance_exists": asterisk_service.ami_client_instance is not None,
            "connected": False,
            "connection_id": None,
            "last_activity": None,
            "event_handlers_count": 0,
            "listener_thread_alive": False
        }
        
        if asterisk_service.ami_client_instance:
            client = asterisk_service.ami_client_instance
            ami_status.update({
                "connected": client.connected,
                "connection_id": getattr(client, 'connection_id', 'N/A'),
                "last_activity": client.last_activity if hasattr(client, 'last_activity') else None,
                "event_handlers_count": len(client.event_handlers) if hasattr(client, 'event_handlers') else 0,
                "listener_thread_alive": (client.listener_thread.is_alive() 
                                        if hasattr(client, 'listener_thread') and client.listener_thread 
                                        else False),
                "host": getattr(client, 'host', 'N/A'),
                "port": getattr(client, 'port', 'N/A'),
                "username": getattr(client, 'username', 'N/A')
            })
        
        return jsonify({
            "success": True,
            "ami_status": ami_status
        })
    except Exception as e:
        logging.error(f"Error getting AMI status: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@call_bp.route("/api/debug/test_ami_connection", methods=["POST"])
@login_required
def test_ami_connection():
    """Test AMI connection and send a simple action"""
    try:
        if not asterisk_service.ami_client_instance:
            return jsonify({"success": False, "message": "No AMI client instance"}), 400
        
        client = asterisk_service.ami_client_instance
        
        # Test connection
        if not client.ensure_connected():
            return jsonify({"success": False, "message": "Failed to establish AMI connection"}), 500
        
        # Send a simple Ping action
        ping_success = client.send_action('Ping')
        
        return jsonify({
            "success": True,
            "connection_test": "passed",
            "ping_sent": ping_success,
            "connection_id": getattr(client, 'connection_id', 'N/A')
        })
        
    except Exception as e:
        logging.error(f"Error testing AMI connection: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500