import json

from datetime import datetime
from collections import defaultdict
from dateutil.parser import parse as dtparse


class PacketInvalidData(Exception):
    """Raised when we try to build a packet with invalid data"""
    pass


class PacketInvalidType(Exception):
    """Raised when we try to create a packet with an invalid type"""


# Closures, for properly handling properties
# in the metaclass
def _make_get_required(key):
    def get_required(self):
        return self._required_data.get(key)
    return get_required


def _make_set_required(key):
    def set_required(self, value):
        self._required_data[key] = value
    return set_required


def _make_del_required(key):
    def del_required(self):
        self._required_data[key] = None
    return del_required


def _make_get_allowed(key):
    def get_allowed(self):
        return self._allowed_data.get(key)
    return get_allowed


def _make_set_allowed(key):
    def set_allowed(self, value):
        self._allowed_data[key] = value
    return set_allowed


class MetaPacket(type):
    """Metaclass for packets.

    Looks at the _data_keys_allowed and _data_keys_required attributes
    when a subclass is declared, then adds class properties that
    stores the information in two separate dictionaries on the object.
    """
    def __new__(cls, name, base, attrs):
        attrs['_required_data'] = {}
        attrs['_allowed_data'] = {}
        required_fields = attrs.get('_data_keys_required', [])
        allowed_fields = attrs.get('_data_keys_allowed', [])
        for k in required_fields:
            attrs[k] = property(_make_get_required(k),
                                _make_set_required(k),
                                _make_del_required(k))
        for k in allowed_fields:
            attrs[k] = property(_make_get_allowed(k), _make_set_allowed(k))
        return type.__new__(cls, name, base, attrs)


class Packet(object, metaclass=MetaPacket):
    """
    Generic AMIE packet base class

    Class parameters:
        _packet_type: the type of the packet (string)
        _expected_reply: expected reply types (list[string] or list[Packet type])
        _data_keys_required: Data keys that are required for this packet type
        _data_keys_allowed: Data keys that are allowed for this packet type


    Args:
        packet_id (str): The ID for this packet
        date (datetime.Datetime): A datetime object representing this packet's date attribute
        additional_data (dict): Body data that is outsite the AMIE spec.
        in_reply_to (str, int, amieclient.Packet): The packet this packet is in response to. Can take a packet, int, string, or None.
    """

    def __init__(self, packet_id=None, date=None,
                 additional_data={}, in_reply_to=None,
                 **kwargs):
        self.packet_id = str(packet_id)
        self.additional_data = additional_data
        if not date:
            self.date = datetime.now()

        if in_reply_to is None or type(in_reply_to) == str:
            # If we're given a string, or None, just use that.
            self.in_reply_to_id = in_reply_to
        elif type(in_reply_to) == int:
            # If it's a int, make it a string
            self.in_reply_to_id = "{}".format(in_reply_to)
        elif hasattr(in_reply_to, 'packet_id'):
            # If we're given a packet object, get the ID
            self.in_reply_to_id = in_reply_to.packet_id
        elif in_reply_to.get('header', {}).get('packet_id'):
            # If we're given a dict-like object, get the ID from the header
            self.in_reply_to_id = in_reply_to['header']['packet_id']
        for key, value in kwargs.items():
            if key in self._data_keys_required or key in self._data_keys_allowed:
                if 'Date' in key:
                    # TODO check if this is a valid assumption
                    setattr(self, key, dtparse(value))
                else:
                    setattr(self, key, value)
            else:
                self.additional_data[key] = value

    @classmethod
    def _find_packet_type(cls, packet_or_packet_type):
        """
        Finds the class for the given packet or packet type
        """
        pkt_cls = None
        if type(packet_or_packet_type) == str:
            # We're given a string, search in
            # subclasses
            for subclass in Packet.__subclasses__():
                if subclass._packet_type == packet_or_packet_type:
                    pkt_cls = subclass
                    break
        elif packet_or_packet_type.__class__ in Packet.__subclasses__():
            # We've been given a packet, just get its class attribute
            pkt_cls = packet_or_packet_type.__class__

        if pkt_cls is None:
            # Raise a NotImplementedError if we can't find a subclass
            error_str = "No packet type matches provided '{}'".format(packet_or_packet_type)
            raise PacketInvalidType(error_str)
        return pkt_cls

    @classmethod
    def from_dict(cls, data):
        """
        Generates an instance of an AMIE packet of this type from provided dictionary

        Args:
            data (dict): Packet data
        """
        # Get the subclass that matches this json input
        pkt_class = cls._find_packet_type(data['type'])

        obj = pkt_class(packet_id=data['header']['packet_id'],
                        in_reply_to=data['header'].get('in_reply_to'),
                        **data['body'])

        # Return an instance of the proper subclass
        return obj

    @classmethod
    def from_json(cls, json_string):
        """
        Generates an instance of an AMIE packet of this type from provided JSON.
        Basically just a wrapper around from_dict.

        Args:
            json_string (string): JSON data
        """
        data = json.loads(json_string)
        return cls.from_dict(data)

    def reply_packet(self, packet_id=None, packet_type=None, force=False):
        """
        Returns a packet that the current packet would expect as a response,
        with the in_reply_to attribute set to the current packet's ID.

        Generally, most packets only have one kind of expected reply,
        so you should be fine to use reply_packet with just the desired packet_id

        Args:
            packet_id: The ID of the reply packet, if needed
            packet_type: Optionally, the type of the reply packet
            force: will create a reply packet whether or not packet_type is in _expected_reply

        Example:
            >>> my_npc = received_rpc.reply_packet()
        """

        if packet_type and force:
            # Just do it
            pkt_class = self._find_packet_type(packet_type)
        elif len(self._expected_reply) == 0:
            # This is a packet that does not expect a response
            raise PacketInvalidType("Packet type '{}' does not expect a reply"
                                    .format(self._packet_type))
        elif len(self._expected_reply) > 1 and packet_type is None:
            # We have more than one expected reply, but no spec'd type
            # to disambiguate
            raise PacketInvalidType("Packet type '{}' has more than one"
                                    " expected response. Specify a packet type"
                                    " for the reply".format(self._packet_type))
        elif packet_type is not None and packet_type not in self._expected_reply:
            raise PacketInvalidType("'{}' is not an expected reply for packet type '{}'"
                                    .format(packet_type, self._packet_type))
        else:
            # We have one packet type, or a specified packet type, and it is valid
            if packet_type is None:
                packet_type = self._expected_reply[0]
            pkt_class = self._find_packet_type(packet_type)
        return pkt_class(packet_id=packet_id, in_reply_to=self.packet_id)

    def as_dict(self):
        """
        This packet, as a dictionary.
        """
        data_body = {}
        # Filter out non-defined items from our data collections, converting
        # if neccessary
        for d in [self._required_data, self._allowed_data, self.additional_data]:
            for k, v in d.items():
                if type(v) == datetime:
                    data_body[k] = v.isoformat()
                elif v is not None:
                    data_body[k] = v

        header = {
            'packet_id': self.packet_id,
            'date': self.date.isoformat(),
            'type': self.packet_type,
            'expected_reply_list': self._expected_reply
        }
        if self.in_reply_to_id:
            header['in_reply_to'] = self.in_reply_to_id
        data_dict = {
            'DATA_TYPE': 'packet',
            'body': data_body,
            'header': header
        }

        return data_dict

    def json(self):
        """
        The JSON representation of this AMIE packet
        """
        data_dict = self.as_dict()
        return json.dumps(data_dict)

    def validate_data(self):
        """
        By default, checks to see that all required data items have a
        defined value, unless in_reply_to is not None (in which case,
        we assume the missing data will be filled in based on the referenced
        packet ID.

        Some packet types will override this function, or add additional checks.
        """
        if self.in_reply_to_id:
            return True
        for k, v in self._required_data.items():
            if v is None:
                raise PacketInvalidData('Missing required data field: "{}"'.format(k))
        return True

    @property
    def packet_type(self):
        """
        The AMIE name for this packet type.
        """
        return self._packet_type
