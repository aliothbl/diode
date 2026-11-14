PLUGINS = [
    "netbox_diode_plugin",
    "netbox_branching",
]

PLUGINS_CONFIG = {
    "netbox_diode_plugin": {
        # Points the plugin's introspection at the benchmark auth stub:
        # http://bench-auth:8080/diode/auth/introspect
        "diode_target_override": "grpc://bench-auth:8080/diode",
        "diode_username": "diode",
        "netbox_to_diode_client_secret": "bench",
    }
}
