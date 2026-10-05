--[[
Dissector de Wireshark para el protocolo RDT del TP1
====================================================

Interpreta el header de 12 bytes de src/lib/protocols/packet/packet.py,
la opción SACK (TLV, tipo 0x01) de selective_ack/sack_option.py y los
mensajes de control de file_transfer.py (UPLOAD, DOWNLOAD, OK, ERROR).

Instalación (Linux):
    mkdir -p ~/.local/lib/wireshark/plugins
    ln -s "$PWD/wireshark/rdt.lua" ~/.local/lib/wireshark/plugins/rdt.lua
Después: Analyze > Reload Lua Plugins (Ctrl+Shift+L), o reiniciar Wireshark.

También se puede cargar sin instalar:
    wireshark -X lua_script:wireshark/rdt.lua -r captura.pcap

Cómo encuentra los paquetes:
  - Puerto UDP del servidor (8080 por defecto, configurable en
    Edit > Preferences > Protocols > RDT).
  - Heurística sobre todo UDP, porque después del SYN el servidor sigue la
    sesión desde un puerto efímero. Cuando un paquete pasa la heurística,
    la conversación entera queda asociada a RDT.

Filtros útiles:
    rdtp                        todo el protocolo
    rdtp.flags.syn == 1         handshake
    rdtp.flags.cancel == 1      avisos de cancelación
    rdtp.sack                   ACKs con bloques SACK
    rdtp.msg.cmd == "UPLOAD"    pedidos de upload
    rdtp.len > 0                segmentos con datos

(El filtro es "rdtp" porque Wireshark ya trae un "rdt", el Real Data
Transport de RealNetworks.)

El modo TCP (protocolo 3) viaja sobre TCP real, sin este header, así que
no lo decodifica este dissector.
]]

local HEADER_SIZE = 12
local RDT_VERSION = 1
local SACK_TYPE = 0x01
local SACK_BLOCK_SIZE = 8
local OPTION_HEADER_SIZE = 2
local DEFAULT_PORT = 8080

local PROTOCOL_NAMES = {
    [1] = "Stop & Wait",
    [2] = "Selective ACK",
    [3] = "TCP",
}

local PROTOCOL_TAGS = {
    [1] = "SW",
    [2] = "SACK",
    [3] = "TCP",
}

local OPTION_NAMES = {
    [SACK_TYPE] = "SACK",
}

-- Mismo orden que flag_names() en packet.py
local FLAG_BITS = {
    { name = "SYN", mask = 0x80 },
    { name = "FIN", mask = 0x40 },
    { name = "ERR", mask = 0x20 },
    { name = "ACK", mask = 0x10 },
    { name = "CANCEL", mask = 0x08 },
}

local rdt = Proto("rdtp", "RDT - Reliable Data Transfer (TP1 Redes)")

local f = rdt.fields
f.version = ProtoField.uint8("rdtp.version", "Version", base.DEC, nil, 0xF0)
f.protocol = ProtoField.uint8("rdtp.protocol", "Protocolo", base.DEC,
    PROTOCOL_NAMES, 0x0F)
f.flags = ProtoField.uint8("rdtp.flags", "Flags", base.HEX)
f.flag_syn = ProtoField.bool("rdtp.flags.syn", "SYN", 8, nil, 0x80)
f.flag_fin = ProtoField.bool("rdtp.flags.fin", "FIN", 8, nil, 0x40)
f.flag_err = ProtoField.bool("rdtp.flags.err", "ERR", 8, nil, 0x20)
f.flag_ack = ProtoField.bool("rdtp.flags.ack", "ACK", 8, nil, 0x10)
f.flag_cancel = ProtoField.bool("rdtp.flags.cancel", "CANCEL", 8, nil, 0x08)
f.flag_reserved = ProtoField.uint8("rdtp.flags.reserved", "Reservados",
    base.HEX, nil, 0x07)
f.hlen = ProtoField.uint8("rdtp.hlen", "Header length", base.DEC)
f.reserved = ProtoField.uint8("rdtp.reserved", "Reservado", base.HEX)
f.seq = ProtoField.uint32("rdtp.seq", "Sequence number", base.DEC)
f.ack = ProtoField.uint32("rdtp.ack", "Acknowledgment number", base.DEC)
f.next_seq = ProtoField.uint32("rdtp.next_seq", "Next sequence number",
    base.DEC)
f.len = ProtoField.uint32("rdtp.len", "Payload length", base.DEC)

f.options = ProtoField.bytes("rdtp.options", "Opciones")
f.option_type = ProtoField.uint8("rdtp.option.type", "Tipo", base.HEX,
    OPTION_NAMES)
f.option_len = ProtoField.uint8("rdtp.option.len", "Longitud", base.DEC)
f.option_data = ProtoField.bytes("rdtp.option.data", "Datos")
f.sack = ProtoField.none("rdtp.sack", "SACK")
f.sack_count = ProtoField.uint8("rdtp.sack.count", "Cantidad de bloques",
    base.DEC)
f.sack_left = ProtoField.uint32("rdtp.sack.left", "Left edge", base.DEC)
f.sack_right = ProtoField.uint32("rdtp.sack.right", "Right edge", base.DEC)
f.sack_size = ProtoField.uint32("rdtp.sack.size", "Bytes en el bloque",
    base.DEC)

f.payload = ProtoField.bytes("rdtp.payload", "Payload")
f.msg = ProtoField.string("rdtp.msg", "Mensaje de control")
f.msg_cmd = ProtoField.string("rdtp.msg.cmd", "Comando")
f.msg_size = ProtoField.uint64("rdtp.msg.size", "Tamaño del archivo", base.DEC)
f.msg_filename = ProtoField.string("rdtp.msg.filename", "Archivo")
f.msg_error = ProtoField.string("rdtp.msg.error", "Motivo")

local ef_cancel = ProtoExpert.new("rdtp.cancel", "Transferencia cancelada",
    expert.group.SEQUENCE, expert.severity.WARN)
local ef_error = ProtoExpert.new("rdtp.err", "Paquete con flag ERR",
    expert.group.RESPONSE_CODE, expert.severity.ERROR)
local ef_bad_hlen = ProtoExpert.new("rdtp.hlen.invalid",
    "Header length inválido", expert.group.MALFORMED, expert.severity.ERROR)
local ef_bad_option = ProtoExpert.new("rdtp.option.invalid",
    "Opción mal formada", expert.group.MALFORMED, expert.severity.ERROR)
local ef_bad_block = ProtoExpert.new("rdtp.sack.invalid",
    "Bloque SACK con left >= right", expert.group.PROTOCOL,
    expert.severity.WARN)
local ef_reserved = ProtoExpert.new("rdtp.reserved.nonzero",
    "Bits reservados distintos de cero", expert.group.PROTOCOL,
    expert.severity.NOTE)
local ef_app_error = ProtoExpert.new("rdtp.msg.error.expert",
    "El otro extremo rechazó el pedido", expert.group.RESPONSE_CODE,
    expert.severity.WARN)

rdt.experts = {
    ef_cancel, ef_error, ef_bad_hlen, ef_bad_option, ef_bad_block,
    ef_reserved, ef_app_error,
}

rdt.prefs.port = Pref.uint("Puerto UDP del servidor", DEFAULT_PORT,
    "Puerto donde escucha el servidor (SERVER_PORT en config_server.env)")

-- Aritmética en vez de operadores de bits: anda igual con Lua 5.2 y 5.4,
-- según la versión con la que esté compilado Wireshark.
local function high_nibble(byte)
    return math.floor(byte / 16)
end

local function low_nibble(byte)
    return byte % 16
end

local function has_flag(flags, mask)
    return math.floor(flags / mask) % 2 == 1
end

local function flag_names(flags)
    local names = {}
    for _, bit in ipairs(FLAG_BITS) do
        if has_flag(flags, bit.mask) then
            names[#names + 1] = bit.name
        end
    end
    return names
end

-- Mensajes de control de file_transfer.py. Son texto corto en un solo
-- segmento, así que exigimos que todo el payload sea texto imprimible
-- para no confundir un pedazo de archivo con un comando.
local function is_printable(text)
    return not text:find("[%z\1-\8\11\12\14-\31\127]")
end

local function dissect_message(payload_tvb, tree)
    local length = payload_tvb:len()
    if length == 0 or length > 1024 then
        return nil
    end

    local text = payload_tvb:raw()
    if not is_printable(text) then
        return nil
    end

    if text == "OK" then
        local msg_tree = tree:add(f.msg, payload_tvb(), text)
        msg_tree:add(f.msg_cmd, payload_tvb(), "OK")
        return "OK"
    end

    local cmd, rest = text:match("^(%u+) (.+)$")
    if cmd == "UPLOAD" then
        local size, filename = rest:match("^(%d+) (.+)$")
        if not size then
            return nil
        end
        local msg_tree = tree:add(f.msg, payload_tvb(), text)
        msg_tree:add(f.msg_cmd, payload_tvb(0, #cmd), cmd)
        msg_tree:add(f.msg_size, payload_tvb(#cmd + 1, #size),
            UInt64.new(tonumber(size)))
        msg_tree:add(f.msg_filename,
            payload_tvb(#cmd + #size + 2, #filename), filename)
        return string.format("UPLOAD %s (%s bytes)", filename, size)
    elseif cmd == "DOWNLOAD" then
        local msg_tree = tree:add(f.msg, payload_tvb(), text)
        msg_tree:add(f.msg_cmd, payload_tvb(0, #cmd), cmd)
        msg_tree:add(f.msg_filename, payload_tvb(#cmd + 1, #rest), rest)
        return "DOWNLOAD " .. rest
    elseif cmd == "ERROR" then
        local msg_tree = tree:add(f.msg, payload_tvb(), text)
        msg_tree:add(f.msg_cmd, payload_tvb(0, #cmd), cmd)
        msg_tree:add(f.msg_error, payload_tvb(#cmd + 1, #rest), rest)
        msg_tree:add_proto_expert_info(ef_app_error, "ERROR: " .. rest)
        return "ERROR " .. rest
    end

    return nil
end

-- Recorre las opciones TLV igual que parse_sack_option(). Devuelve los
-- bloques para armar la columna Info.
local function dissect_options(options_tvb, tree)
    local options_tree = tree:add(f.options, options_tvb())
    local total = options_tvb:len()
    local offset = 0
    local blocks = {}

    while offset < total do
        if offset + OPTION_HEADER_SIZE > total then
            options_tree:add_proto_expert_info(ef_bad_option,
                "Sobran bytes que no alcanzan para una opción")
            break
        end

        local option_type = options_tvb(offset, 1):uint()
        local option_len = options_tvb(offset + 1, 1):uint()

        if option_len < OPTION_HEADER_SIZE or offset + option_len > total then
            local item = options_tree:add(f.option_len,
                options_tvb(offset + 1, 1))
            item:add_proto_expert_info(ef_bad_option, string.format(
                "Longitud %d fuera de rango (quedan %d bytes)",
                option_len, total - offset))
            break
        end

        local option_range = options_tvb(offset, option_len)

        if option_type == SACK_TYPE then
            local body_len = option_len - OPTION_HEADER_SIZE
            local count = math.floor(body_len / SACK_BLOCK_SIZE)
            local sack_tree = options_tree:add(f.sack, option_range)
            sack_tree:set_text(string.format("SACK: %d bloque%s", count,
                count == 1 and "" or "s"))
            sack_tree:add(f.option_type, options_tvb(offset, 1))
            sack_tree:add(f.option_len, options_tvb(offset + 1, 1))
            local count_item = sack_tree:add(f.sack_count,
                options_tvb(offset + 1, 1), count)
            count_item:set_generated()

            if body_len % SACK_BLOCK_SIZE ~= 0 then
                sack_tree:add_proto_expert_info(ef_bad_option,
                    "La longitud no es 2 + 8*N")
            end

            for i = 0, count - 1 do
                local block_offset = offset + OPTION_HEADER_SIZE
                    + i * SACK_BLOCK_SIZE
                local left_range = options_tvb(block_offset, 4)
                local right_range = options_tvb(block_offset + 4, 4)
                local left = left_range:uint()
                local right = right_range:uint()

                local block_tree = sack_tree:add(rdt,
                    options_tvb(block_offset, SACK_BLOCK_SIZE),
                    string.format("Bloque %d: [%d, %d)", i + 1, left, right))
                block_tree:add(f.sack_left, left_range)
                block_tree:add(f.sack_right, right_range)

                if left < right then
                    local size_item = block_tree:add(f.sack_size,
                        options_tvb(block_offset, SACK_BLOCK_SIZE),
                        right - left)
                    size_item:set_generated()
                    blocks[#blocks + 1] = string.format("%d-%d", left, right)
                else
                    block_tree:add_proto_expert_info(ef_bad_block)
                end
            end
        else
            local option_tree = options_tree:add(rdt, option_range,
                string.format("Opción desconocida (tipo 0x%02x)", option_type))
            option_tree:add(f.option_type, options_tvb(offset, 1))
            option_tree:add(f.option_len, options_tvb(offset + 1, 1))
            if option_len > OPTION_HEADER_SIZE then
                option_tree:add(f.option_data, options_tvb(
                    offset + OPTION_HEADER_SIZE,
                    option_len - OPTION_HEADER_SIZE))
            end
        end

        offset = offset + option_len
    end

    return blocks
end

function rdt.dissector(tvb, pinfo, tree)
    local length = tvb:len()
    if length < HEADER_SIZE then
        return 0
    end

    local first_byte = tvb(0, 1):uint()
    local protocol_id = low_nibble(first_byte)
    local flags = tvb(1, 1):uint()
    local hlen = tvb(2, 1):uint()
    local seq = tvb(4, 4):uint()
    local ack = tvb(8, 4):uint()

    pinfo.cols.protocol = "RDT-" .. (PROTOCOL_TAGS[protocol_id] or "?")

    local header_len = math.max(HEADER_SIZE, math.min(hlen, length))
    local subtree = tree:add(rdt, tvb(0, header_len))

    subtree:add(f.version, tvb(0, 1))
    subtree:add(f.protocol, tvb(0, 1))

    local names = flag_names(flags)
    local flags_text = #names > 0 and table.concat(names, ", ") or "-"
    local flags_tree = subtree:add(f.flags, tvb(1, 1))
    flags_tree:append_text(" (" .. flags_text .. ")")
    flags_tree:add(f.flag_syn, tvb(1, 1))
    flags_tree:add(f.flag_fin, tvb(1, 1))
    flags_tree:add(f.flag_err, tvb(1, 1))
    flags_tree:add(f.flag_ack, tvb(1, 1))
    flags_tree:add(f.flag_cancel, tvb(1, 1))
    local reserved_flags = flags_tree:add(f.flag_reserved, tvb(1, 1))
    if flags % 8 ~= 0 then
        reserved_flags:add_proto_expert_info(ef_reserved)
    end

    local hlen_item = subtree:add(f.hlen, tvb(2, 1))
    local reserved_item = subtree:add(f.reserved, tvb(3, 1))
    if tvb(3, 1):uint() ~= 0 then
        reserved_item:add_proto_expert_info(ef_reserved)
    end
    subtree:add(f.seq, tvb(4, 4))
    subtree:add(f.ack, tvb(8, 4))

    local info = {}
    if #names > 0 then
        info[#info + 1] = "[" .. table.concat(names, ",") .. "]"
    end
    info[#info + 1] = "Seq=" .. seq
    if has_flag(flags, 0x10) then
        info[#info + 1] = "Ack=" .. ack
    end

    if hlen < HEADER_SIZE or hlen > length then
        hlen_item:add_proto_expert_info(ef_bad_hlen, string.format(
            "Header length %d inválido (mínimo %d, paquete de %d bytes)",
            hlen, HEADER_SIZE, length))
        pinfo.cols.info = table.concat(info, " ") .. " [header inválido]"
        return length
    end

    if hlen > HEADER_SIZE then
        local options_tvb = tvb(HEADER_SIZE, hlen - HEADER_SIZE):tvb()
        local blocks = dissect_options(options_tvb, subtree)
        if #blocks > 0 then
            info[#info + 1] = "SACK=" .. table.concat(blocks, ",")
        end
    end

    local payload_len = length - hlen
    local len_item = subtree:add(f.len, tvb(2, 1), payload_len)
    len_item:set_generated()
    info[#info + 1] = "Len=" .. payload_len

    -- Numeración por bytes, como en stop_wait.py: el SYN y un FIN vacío
    -- consumen un número de secuencia.
    local consumed = payload_len
    if consumed == 0 and (has_flag(flags, 0x80) or has_flag(flags, 0x40)) then
        consumed = 1
    end
    if consumed > 0 then
        local next_item = subtree:add(f.next_seq, tvb(4, 4), seq + consumed)
        next_item:set_generated()
    end

    if has_flag(flags, 0x20) and has_flag(flags, 0x08) then
        flags_tree:add_proto_expert_info(ef_cancel)
    elseif has_flag(flags, 0x20) then
        flags_tree:add_proto_expert_info(ef_error)
    end

    if payload_len > 0 then
        local payload_tvb = tvb(hlen, payload_len):tvb()
        local payload_tree = subtree:add(f.payload, payload_tvb())
        local message = dissect_message(payload_tvb, payload_tree)
        if message then
            info[#info + 1] = message
        end
    end

    pinfo.cols.info = table.concat(info, " ")
    return length
end

-- La heurística replica is_valid() de packet.py y además exige versión,
-- protocolo y bits reservados conocidos, para no adueñarse de cualquier
-- datagrama UDP (DNS, mDNS, etc.).
local function looks_like_rdt(tvb)
    local length = tvb:len()
    if length < HEADER_SIZE then
        return false
    end

    local first_byte = tvb(0, 1):uint()
    if high_nibble(first_byte) ~= RDT_VERSION then
        return false
    end
    local protocol_id = low_nibble(first_byte)
    if protocol_id ~= 1 and protocol_id ~= 2 then
        return false
    end
    if tvb(1, 1):uint() % 8 ~= 0 or tvb(3, 1):uint() ~= 0 then
        return false
    end

    local hlen = tvb(2, 1):uint()
    if hlen < HEADER_SIZE or hlen > length then
        return false
    end

    -- Si hay opciones, tienen que ser SACK con longitud 2 + 8*N
    local offset = HEADER_SIZE
    while offset < hlen do
        if offset + OPTION_HEADER_SIZE > hlen then
            return false
        end
        local option_type = tvb(offset, 1):uint()
        local option_len = tvb(offset + 1, 1):uint()
        if option_type ~= SACK_TYPE
            or option_len < OPTION_HEADER_SIZE + SACK_BLOCK_SIZE
            or (option_len - OPTION_HEADER_SIZE) % SACK_BLOCK_SIZE ~= 0
            or offset + option_len > hlen then
            return false
        end
        offset = offset + option_len
    end

    return true
end

local function heuristic_dissector(tvb, pinfo, tree)
    if not looks_like_rdt(tvb) then
        return false
    end
    -- El resto de la conversación (puerto efímero del servidor) va directo
    -- a RDT sin volver a pasar por la heurística.
    pinfo.conversation = rdt
    rdt.dissector(tvb, pinfo, tree)
    return true
end

local udp_port_table = DissectorTable.get("udp.port")
local registered_port = rdt.prefs.port
udp_port_table:add(registered_port, rdt)
rdt:register_heuristic("udp", heuristic_dissector)

function rdt.prefs_changed()
    if registered_port ~= rdt.prefs.port then
        if registered_port ~= 0 then
            udp_port_table:remove(registered_port, rdt)
        end
        registered_port = rdt.prefs.port
        if registered_port ~= 0 then
            udp_port_table:add(registered_port, rdt)
        end
    end
end
