from mininet.topo import Topo
from mininet.link import TCLink


class Topologia5Host3Switch(Topo):
    """
    h1, h2 -- s1 -- s2 -- s3 -- h5
    h3, h4 -- s2
    """

    def __init__(self, delay='20ms', perdida=10, mtu=1500):
        Topo.__init__(self)

        perdida = float(perdida)

        # Switches
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')

        # Hosts
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        h3 = self.addHost('h3')
        h4 = self.addHost('h4')
        h5 = self.addHost('h5')

        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(h3, s2)
        self.addLink(h4, s2)

        self.addLink(s1, s2)
        self.addLink(s2, s3)

        self.addLink(
            h5, s3,
            cls=TCLink,
            loss=perdida,
            delay=delay,
            mtu=mtu
        )


topos = {
    'Topologia5Host3Switch': Topologia5Host3Switch
}
