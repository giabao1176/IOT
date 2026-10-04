import pytest

from export_encoder import export_encoder_model


def test_export_rejects_missing_checkpoint_without_creating_model(tmp_path):
    output = tmp_path / "encoder.pt"
    with pytest.raises(FileNotFoundError):
        export_encoder_model(str(tmp_path / "missing.pt"), str(output))
    assert not output.exists()
