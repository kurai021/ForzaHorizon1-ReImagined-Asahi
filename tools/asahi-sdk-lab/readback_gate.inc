// Members of the existing lab-only friend; no renderer or synchronization changes.
template <typename Writer>
static void ConfigureResolveGate(rex::memory::Memory& memory, Writer write,
                                 uint32_t destination, uint32_t pitch, uint32_t height) {
  constexpr uint32_t vertices_address = 0x04000000;
  const float vertices[6]{-0.5f, -0.5f, 63.5f, -0.5f, -0.5f, 63.5f};
  std::memcpy(memory.TranslatePhysical(vertices_address), vertices, sizeof(vertices));
  xenos::xe_gpu_vertex_fetch_t fetch{};
  fetch.type = xenos::FetchConstantType::kVertex;
  fetch.address = vertices_address / 4; fetch.size = 6; fetch.endian = xenos::Endian::kNone;
  write(XE_GPU_REG_SHADER_CONSTANT_FETCH_00_0, fetch.dword_0);
  write(XE_GPU_REG_SHADER_CONSTANT_FETCH_00_0 + 1, fetch.dword_1);
  reg::RB_COPY_CONTROL control{};
  control.copy_command = xenos::CopyCommand::kRaw;
  control.copy_sample_select = xenos::CopySampleSelect::k0;
  write(reg::RB_COPY_CONTROL::register_index, control.value);
  reg::RB_COPY_DEST_INFO info{};
  info.copy_dest_format = xenos::ColorFormat::k_8_8_8_8;
  write(reg::RB_COPY_DEST_INFO::register_index, info.value);
  reg::RB_COPY_DEST_PITCH size{};
  size.copy_dest_pitch = pitch; size.copy_dest_height = height;
  write(reg::RB_COPY_DEST_PITCH::register_index, size.value);
  write(XE_GPU_REG_RB_COPY_DEST_BASE, destination);
  write(XE_GPU_REG_RB_SURFACE_INFO, 1280);
  write(XE_GPU_REG_PA_SU_VTX_CNTL, 0);
  write(XE_GPU_REG_PA_SU_SC_MODE_CNTL, 0);
  write(XE_GPU_REG_PA_SC_WINDOW_OFFSET, 0);
  write(XE_GPU_REG_PA_SC_SCREEN_SCISSOR_TL, 0);
  write(XE_GPU_REG_PA_SC_SCREEN_SCISSOR_BR, 64 | (64u << 16));
  write(XE_GPU_REG_PA_SC_WINDOW_SCISSOR_TL, 0);
  write(XE_GPU_REG_PA_SC_WINDOW_SCISSOR_BR, 64 | (64u << 16));
}

static draw_util::ResolveInfo GateLayout(const RegisterFile& registers,
                                        rex::memory::Memory& memory, uint32_t destination) {
  draw_util::ResolveInfo info{};
  if (!draw_util::GetResolveInfo(registers, memory, 1, 1, false, false, info) ||
      info.coordinate_info.width_div_8 != 8 || info.height_div_8 != 8 ||
      info.copy_dest_extent_start != destination || !info.copy_dest_extent_length ||
      info.copy_dest_extent_length > 1024 * 1024)
    throw std::runtime_error("resolve gate layout outside bounded synthetic rectangle");
  return info;
}

static void VerifyResolveLayoutCpu() {
  rex::memory::Memory memory;
  if (!memory.Initialize()) throw std::runtime_error("resolve layout memory initialization");
  RegisterFile registers;
  for (const auto& shape : {std::array<uint32_t, 3>{0x03000000, 64, 64},
                           std::array<uint32_t, 3>{0x05000000, 768, 288}}) {
    ConfigureResolveGate(memory, [&](uint32_t r, uint32_t v) { registers[r] = v; },
                         shape[0], shape[1], shape[2]);
    auto info = GateLayout(registers, memory, shape[0]);
    for (uint32_t y = 0; y < 64; ++y) for (uint32_t x = 0; x < 64; ++x) {
      const uint32_t offset = texture_util::GetTiledOffset2D(x, y, shape[1], 2);
      if (uint64_t(offset) + 4 > info.copy_dest_extent_length)
        throw std::runtime_error("resolve fixture pixel outside written range");
    }
    std::printf("LAB RESOLVE LAYOUT PASS pitch=%u height=%u: 64x64, %u written bytes, all tiled offsets bounded\n",
                shape[1], shape[2], info.copy_dest_extent_length);
  }
}

static void ReadResolveGate(VulkanCommandProcessor& commands, uint32_t red_x, uint32_t red_y) {
  if (red_x >= 64 || red_y >= 64 || !commands.readback_profile_ ||
      rex::cvar::GetFlagByName("readback_resolve") != "some" ||
      std::stod(rex::cvar::GetFlagByName("fh1_readback_profile_start")) != 0)
    throw std::runtime_error("resolve gate requires recorded native RGBA8 fixture and some mode");
  const auto trace_path = rex::cvar::GetFlagByName("fh1_readback_profile_csv");
  uint64_t end_ns = 0;
  {
    std::ifstream header(trace_path);
    std::string line;
    while (std::getline(header, line)) {
      if (line.starts_with("#window_end_ns=")) end_ns = std::stoull(line.substr(15));
      if (!line.starts_with('#')) break;
    }
  }
  if (!end_ns || readback_profile::WallNow() + 1000000000ull >= end_ns)
    throw std::runtime_error("resolve gate capture window too short; no automatic retry");
  constexpr uint32_t guard = 64;
  for (bool thumbnail : {false, true}) {
    const uint32_t address = thumbnail ? 0x05000000 : 0x03000000;
    const uint32_t pitch = thumbnail ? 768 : 64;
    ConfigureResolveGate(*commands.memory_, [&](uint32_t r, uint32_t v) { commands.WriteRegister(r, v); },
                         address, pitch, thumbnail ? 288 : 64);
    const auto info = GateLayout(*commands.register_file_, *commands.memory_, address);
    const uint32_t length = info.copy_dest_extent_length;
    auto* bytes = commands.memory_->TranslatePhysical(address);
    std::memset(bytes - guard, 0xa5, length + 2 * guard);
    const auto verify = [&] {
      const uint32_t offset = texture_util::GetTiledOffset2D(red_x, red_y, pitch, 2);
      const uint8_t red[4]{255, 0, 0, 255};
      if (uint64_t(offset) + 4 > length || std::memcmp(bytes + offset, red, 4))
        throw std::runtime_error("actual resolve did not preserve independently verified red pixel");
      for (uint32_t i = 0; i < guard; ++i)
        if (bytes[int32_t(i) - int32_t(guard)] != 0xa5 || bytes[length + i] != 0xa5)
          throw std::runtime_error("actual resolve changed neighboring CPU sentinel bytes");
    };
    const uint64_t copied_before = fh1_frame_cost::Current().readbacks;
    if (!commands.IssueCopy() || !commands.AwaitAllQueueOperationsCompletion())
      throw std::runtime_error("actual resolve first-copy submission");
    verify();
    if (fh1_frame_cost::Current().readbacks != copied_before + 1)
      throw std::runtime_error("actual resolve first copy was not recorded");
    const std::vector<uint8_t> expected(bytes, bytes + length);
    if (thumbnail) std::memset(bytes, 0x3c, length);
    if (!commands.IssueCopy() || !commands.AwaitAllQueueOperationsCompletion())
      throw std::runtime_error("actual resolve repeated-copy submission");
    verify();
    if (std::memcmp(bytes, expected.data(), length) ||
        fh1_frame_cost::Current().readbacks != copied_before + (thumbnail ? 2 : 1))
      throw std::runtime_error("actual resolve repeated copy/skip changed bytes or copy count");
    std::printf("LAB RESOLVE PASS %s: real IssueCopy, %u-byte mirror, RGBA pixel, 128 sentinel bytes, repeated %s verified\n",
                thumbnail ? "thumbnail-full" : "some-miss-hit", length,
                thumbnail ? "fresh copy" : "CPU-copy skip");
  }
  // One bounded exporter grace on the owner, with no GPU work, polling or shutdown hook.
  const auto deadline = std::chrono::steady_clock::time_point(std::chrono::nanoseconds(end_ns));
  std::this_thread::sleep_until(deadline + std::chrono::milliseconds(5));
  if (!commands.readback_profile_->FinalizeIfReady()) throw std::runtime_error("resolve gate trace export");
  std::puts("LAB RESOLVE EXPORT PASS: completed on command-processor owner before ordinary teardown");
}
