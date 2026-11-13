"""Trust explicit proxy networks, never the presence of forwarding headers."""
import ipaddress
import os

def client_ip(peer, headers):
    try: address=ipaddress.ip_address(peer)
    except ValueError: return 'unknown'
    try:
        trusted=[ipaddress.ip_network(x.strip()) for x in os.environ.get('TABO_TRUSTED_PROXY_CIDRS','').split(',') if x.strip()]
    except ValueError:
        return address.compressed  # Invalid configuration fails closed.
    def is_trusted(value):
        return any(value.version==network.version and value in network for network in trusted)
    if not is_trusted(address): return address.compressed
    values=headers.get_all('X-Forwarded-For',[]) if hasattr(headers,'get_all') else [headers.get('X-Forwarded-For','')]
    if len(values)!=1: return address.compressed
    chain=values[0].split(',')
    if not 1<=len(chain)<=20: return address.compressed
    try: chain=[ipaddress.ip_address(x.strip()) for x in chain]
    except ValueError: return address.compressed
    # Strip only verified hops from the right, stopping at the first untrusted peer.
    for candidate in reversed(chain):
        if not is_trusted(address): break
        address=candidate
    return address.compressed
